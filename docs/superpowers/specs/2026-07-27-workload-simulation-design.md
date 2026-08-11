# Workload Simulation Framework Design

> Date: 2026-07-27
> Status: Draft
> Author: Collaborative design session

## 1. Problem Statement

### Context

芯片厂商需要基于客户真实软件的 workload 特征来指导：
- 自家软件（编译器、基础库、runtime）优化
- 下一代芯片微架构演进决策

但客户软件通常是大型私有的 C++ 项目（搜推编排引擎、排序服务等），无法直接获取源码。只能拿到客户现场的性能采集数据：火焰图、Topdown 分析、内存特征、业务描述、开源基础库版本、已验证的优化策略等。

### Goal

构建一个 **Workload Simulation Framework**，能够：
1. 解析客户现场数据，提取结构化 Profile
2. 基于 Agent 自动生成 C++ workload 程序 + 压测程序
3. 在开源基础软件（folly/fbthrift/taskflow/brpc 等）的调用栈上尽可能对齐客户真实软件
4. 使生成 workload 在微架构特征上（Topdown、内存带宽、热点覆盖率）模拟客户真实软件
5. 自动迭代优化直到收敛

### Success Criteria

| Metric | Target | Phase |
|--------|--------|-------|
| Topdown 四项误差 | < 10% | 迭代目标 |
| 内存带宽误差 | < 5% | 迭代目标 |
| 开源库热点覆盖率 | > 80% | 迭代目标 |
| 可运行端到端闭环 | 先跑通 | Phase 1 |

### Constraints

- **目标平台**: ARM64（ARM Neoverse N1/V1/N2/V2），有自研 devkit 采集工具
- **语言栈**: C++ 优先，后续扩展 Go/Java/Python
- **客户数据**: 异构（火焰图、Topdown CSV/JSON、业务描述 Markdown、优化策略记录、开源库版本信息）
- **部署**: 用户提供部署脚本和依赖信息，Harness 只负责执行
- **客户自研代码**: 不可获取，100% 复刻不可能，目标是开源库层对齐

---

## 2. Architecture Overview

```
┌───────────────────────────────────────────────────────────────┐
│                  Workload Simulation Framework                  │
│                                                                │
│  ┌──────────────┐   ┌───────────────┐   ┌──────────────────┐ │
│  │   Data       │   │   Agent Core  │   │   Harness        │ │
│  │   Ingestion  │──▶│  (Orchestrator)│──▶│  (Execution)     │ │
│  └──────────────┘   └───────────────┘   └──────────────────┘ │
│       │                  │                      │              │
│       ▼                  ▼                      ▼              │
│  ┌──────────────┐   ┌───────────────┐   ┌──────────────────┐ │
│  │   Profile    │   │   Code Gen    │   │   Collect &      │ │
│  │   Store      │   │   Engine      │   │   Compare        │ │
│  └──────────────┘   └───────────────┘   └──────────────────┘ │
│                        │                      │               │
│                        └──────┬────────────────┘               │
│                               ▼                                │
│                        ┌──────────────┐                        │
│                        │  Iteration   │                        │
│                        │  Loop        │                        │
│                        └──────────────┘                        │
└───────────────────────────────────────────────────────────────┘
```

### Five Core Components

| Component | Role |
|-----------|------|
| **Data Ingestion** | 解析客户异构数据 → 结构化 Profile |
| **Profile Store** | 存储和查询 Profile，支持客户 vs workload 对比 |
| **Agent Core** | AI Agent 编排器：分析 → 策略 → 生成指令 → 评估 → 迭代决策 |
| **Code Gen Engine** | 五层代码生成，产出 workload + stress + config |
| **Harness (Execution)** | 编译/部署/执行/采集/对比，暴露 MCP 工具给 Agent |

### Data Flow

```
客户数据 → Data Ingestion → Profile Store → Agent Core 分析
    → Code Gen Engine 生成代码 → Harness 编译/部署/执行/采集
    → Profile Comparator 对比 → Agent Core 评估 → 是否迭代?
        → 是: 调整策略 (调参 > 调行为 > 调 Workflow > 调架构)
        → 否: 输出最终 workload + 压测程序 + 对齐报告
```

---

## 3. Data Ingestion

### Supported Input Formats

| Input Type | Format | Extracted Content |
|------------|--------|-------------------|
| 火焰图 | 折叠格式 `.txt` / flamegraph.pl `.svg` / perf script 输出 | 调用栈树、函数热点占比、调用频率 |
| Topdown 分析 | JSON/CSV (devkit 输出) | Frontend Bound / Backend Bound / Bad Speculation / Retiring 比例 |
| 内存特征 | JSON/CSV | 内存带宽、cache miss rate、TLB miss rate |
| 业务描述 | Markdown / 自由文本 | 业务逻辑、架构描述、关键调用链 |
| 开源库版本 | JSON/文本 | folly/fbthrift/brpc 版本、编译选项 |
| 优化策略 | Markdown/表格 | 已验证策略和效果（如 jemalloc 后台线程 +x%） |

> **实现状态（火焰图）**：`FlamegraphParser` 按文件后缀分派——`.txt` 走折叠格式解析；`.svg` 走 flamegraph.pl SVG 空间重建。从每个 `<g class="func_g">` 的 `<title>`（函数名 + 采样计数）与 `<rect>`（x/y/width/height）按“下方最近且 x 区间包含”原则重建父链，inclusive 计数取自 title（缺失时按相对根宽推导），self = inclusive − Σ 子节点 inclusive，每个 self>0 输出一行折叠栈，复用现有 hotspot / 调用树 / 结构对齐管线，下游消费者无需改动。仅支持 flamegraph.pl 标准布局（非 inverted/icicle）；flamegraph.pl 会按宽度截断长函数名，分类器正则可能因此失配——优先使用原始折叠/perf 数据。

### Profile Schema

```json
{
  "metadata": {
    "customer": "xxx",
    "date": "2026-07-27",
    "platform": "arm64",
    "kernel_version": "5.15.x",
    "neoverse_core": "N2",
    "software_stack": [
      { "name": "folly", "version": "2.1.0", "compile_flags": "-O2 -march=armv8.2-a" },
      { "name": "fbthrift", "version": "1.5.0" },
      { "name": "jemalloc", "version": "5.3.0", "config": { "bg_thread": true } }
    ]
  },
  "hotspots": [
    {
      "function": "folly::futures::detail::FutureImpl::then",
      "library": "folly",
      "source": "open_source",
      "self_pct": 12.5,
      "cumulative_pct": 35.2,
      "call_path": [
        "main",
        "Server::handleRequest",
        "AsyncProcessor::process",
        "folly::futures::detail::FutureImpl::then"
      ]
    }
  ],
  "topdown": {
    "frontend_bound": 0.25,
    "backend_bound": 0.40,
    "bad_speculation": 0.10,
    "retiring": 0.25
  },
  "topdown_l2": {
    "frontend_bound": {
      "branch_detect": 0.05,
      "fetch_latency": 0.15,
      "icache_misses": 0.05
    },
    "backend_bound": {
      "memory_bound": 0.30,
      "core_bound": 0.10
    },
    "bad_speculation": {
      "branch_mispredict": 0.08,
      "other": 0.02
    },
    "retiring": {
      "heavy_ops": 0.15,
      "light_ops": 0.10
    }
  },
  "memory": {
    "bandwidth_gbps": 45.2,
    "l3_miss_rate": 0.08,
    "tlb_miss_rate": 0.02,
    "working_set_size_mb": 512
  },
  "optimizations": [
    {
      "strategy": "jemalloc background thread",
      "impact": "+5% throughput",
      "verified": true,
      "context": "减少内存碎片导致的 backend bound"
    }
  ],
  "business_logic": "高并发 RPC 服务，处理请求后做异步计算...",
  "callgraph_summary": {
    "total_unique_functions": 1200,
    "open_source_functions": 400,
    "customer_custom_functions": 800,
    "open_source_hotspot_pct": 60,
    "customer_custom_hotspot_pct": 40
  }
}
```

### Parsers

| Parser | Input | Output | Implementation |
|--------|-------|--------|---------------|
| **FlamegraphParser** | perf script / 折叠格式 | 调用栈树 + 热点列表 | Python, 解析火焰图文本格式 |
| **TopdownParser** | devkit JSON/CSV | Topdown Level 1 + Level 2 | Python, 解析 ARM Topdown 格式 |
| **MemoryParser** | devkit JSON/CSV | 内存带宽 + cache/TLB 特征 | Python |
| **TextParser** | Markdown / 自由文本 | 业务逻辑结构化描述 | LLM-assisted (Agent 负责) |
| **VersionParser** | JSON/文本 | 开源库版本 + 编译选项 | Python |

---

## 4. Agent Core

### Role

Agent 是整个框架的"大脑"，负责：
1. **Profile 分析** — 识别关键特征、瓶颈、可复现热点
2. **生成策略制定** — 划分 Business Workflow Stage + Behavior Profile
3. **代码生成指令下发** — 给 Code Gen Engine 下发结构化生成任务
4. **迭代决策** — 读取对比结果，决定调整方向和力度

### Agent Selection

- **初期**: Claude / GPT 等 LLM，通过 MCP 协议暴露 Harness 工具
- **后续**: 可替换为自研 Agent 或其他框架（opencode 等），只要适配 MCP 接口

Agent 选型不影响框架核心设计——MCP 协议是 Agent 和 Harness 之间的稳定接口。

### Agent Tools (MCP)

| Tool | Function | Returns |
|------|----------|---------|
| `query_profile` | 查询客户 Profile 数据 | Profile JSON |
| `analyze_hotspots` | 分析热点函数，分类开源/自研 | 分类结果 |
| `plan_workflow` | 规划 Business Workflow 划分 | Workflow plan |
| `generate_workload` | 下发代码生成任务 | 项目路径 |
| `build_workload` | 编译 workload | 编译结果 (成功/失败) |
| `deploy_workload` | 部署到目标机器 (用户提供配置) | 部署结果 |
| `run_workload` | 执行 workload (含 warmup) | 运行结果 |
| `collect_metrics` | 采集 Topdown + 火焰图 + 内存 | 采集数据路径 |
| `compare_profiles` | 对比客户 Profile vs workload Profile | 对比报告 |
| `adjust_config` | 调整 config.json 参数 (无需重编译) | 调整结果 |
| `list_open_source_libs` | 列出可用开源库及版本 | 库列表 |

### Agent Decision Logic

```
Iteration Strategy (优先级递减, 成本递增):

Priority 1: 调 config.json 参数
  → 0 编译成本, 最快迭代
  → 适用: Topdown 比例偏移不大 (< 5%), 只需调并发/QPS/内存比例

Priority 2: 调 Behavior Profile
  → 局部代码修改, 快速重编译
  → 适用: 单个 Stage 的微架构特征不对, 需要换行为实现策略

Priority 3: 调 Business Workflow
  → 中等修改, 改阶段组合/数据流
  → 适用: 整体 Topdown 分布偏移大 (> 10%), 需要重新划分 Stage

Priority 4: 调 Service Skeleton
  → 大修改, 改架构拓扑
  → 适用: 基本架构假设错误 (如单线程 vs 多线程模型不对)
```

---

## 5. Code Gen Engine

### Five-Layer Generation Architecture

```
┌──────────────────────────────────────────────┐
│  Layer 0: Project Scaffold                    │
│  - 多模块项目结构 (CMake superproject)        │
│  - 依赖管理: folly/fbthrift/brpc 等多版本    │
│  - 编译选项对齐 (与客户一致的 -O2/-march等)   │
│  - 文件夹组织与模块划分                        │
├──────────────────────────────────────────────┤
│  Layer 1: Service Skeleton                    │
│  - 多组件关系与架构拓扑                        │
│  - 负载 workload 主体确定                      │
│  - 压测模型 (client/server/standalone)         │
│  - 线程模型与资源管理 (线程池/连接池)          │
├──────────────────────────────────────────────┤
│  Layer 2: Business Workflow                   │
│  - 各模块间调用顺序与数据流向                   │
│  - 业务流程编排 (如搜推: 特征→推理→排序→去重)  │
│  - 覆盖多种业务场景 (搜推/推荐/排序/前端处理等) │
├──────────────────────────────────────────────┤
│  Layer 3: Behavior Profiles                   │
│  - 每个阶段/模块的微架构行为描述               │
│  - 行为实现策略选择:                            │
│    a) 直接调用开源库函数 (热点对齐)             │
│    b) 计算合成 (模拟计算密集行为)               │
│    c) 内存合成 (模拟内存密集行为)               │
│    d) 混合 (组合上述)                           │
│  - 基于 Topdown L1/L2 推断瓶颈类型和权重       │
├──────────────────────────────────────────────┤
│  Layer 4: Tuning Knobs                        │
│  - 并发度/线程数/QPS/请求大小                   │
│  - 各阶段耗时比例/内存分配比例                   │
│  - 数据局部性控制 (随机 vs 顺序访问)             │
│  - 开源库参数 (jemalloc 配置等)                 │
│  - 通过 config.json 控制, 无需重编译            │
└──────────────────────────────────────────────┤
```

### Behavior Profile Per Stage/Module

```json
{
  "stage_name": "feature_extraction",
  "target_topdown": {
    "frontend_bound": 0.30,
    "backend_bound": 0.45,
    "bad_speculation": 0.05,
    "retiring": 0.20
  },
  "target_topdown_l2": {
    "backend_bound": { "memory_bound": 0.35, "core_bound": 0.10 },
    "frontend_bound": { "fetch_latency": 0.20, "branch_detect": 0.10 }
  },
  "implementation_strategy": "mixed",
  "strategies": [
    {
      "function": "folly::futures::detail::FutureImpl::then",
      "source": "open_source",
      "strategy": "direct_call",
      "weight_pct": 12.5
    },
    {
      "function": "FeatureHash::compute",
      "source": "customer_custom",
      "strategy": "compute_synthesis",
      "weight_pct": 8.0,
      "synthesis_config": { "compute_type": "hash", "iterations": 100 }
    },
    {
      "function": "FeatureStore::lookup",
      "source": "customer_custom",
      "strategy": "memory_synthesis",
      "weight_pct": 15.0,
      "synthesis_config": { "access_pattern": "random", "working_set_mb": 64 }
    }
  ],
  "data_scale": { "working_set_mb": 128, "data_structure": "hash_map" },
  "concurrency": { "mode": "async", "thread_count": 8 }
}
```

### Behavior Implementation Strategy Selection Rules

| 客户火焰图中该函数 | 来源 | 实现策略 |
|------------------|------|---------|
| folly::futures::detail::FutureImpl::then | 开源库热点 | **direct_call**: 直接调用 folly Future API |
| fbthrift::RocketServer::handleRequest | 开源库热点 | **direct_call**: 启动 fbthrift server |
| CustomerCustom::featureCalc | 客户自研 | **compute_synthesis**: 用计算密集代码模拟 |
| CustomerCustom::dataLookup | 客户自研 | **memory_synthesis**: 用随机内存访问模拟 |
| CustomerCustom::pipelineMerge | 客户自研 | **mixed**: 组合计算+内存行为 |

**核心原则**: 开源库热点函数直接真实调用（保证调用栈路径一致），客户自研代码用合成行为模拟（保证微架构特征一致）。

### Code Generation Flow

```
Agent 下发生成指令 (结构化 JSON)
    → Skeleton Generator: 基于 Layer 0-1 模板生成项目骨架
    → Workflow Generator: 基于 Layer 2 生成 Business Workflow 代码
    → Behavior Generator: 基于 Layer 3 Behavior Profiles 生成各阶段实现
    → Knob Generator: 基于 Layer 4 生成 config.json + 参数解析代码
    → Validator: 编译检查 + 静态分析 (编译能否通过、API 是否存在)
    → Output: workload binary + stress binary + config.json + CMakeLists.txt
```

### Stress Program Generation

压测程序与 workload 程序配套生成：

```json
{
  "stress_config": {
    "mode": "rpc_client",  // 或 standalone_runner
    "target_host": "localhost:8080",
    "concurrency": [4, 8, 16, 32],
    "duration_seconds": 60,
    "warmup_seconds": 30,
    "request_pattern": "poisson",  // 或 uniform, burst
    "request_size_bytes": 1024,
    "ramp_up_seconds": 10
  }
}
```

---

## 6. Harness (Execution)

### Components

| Component | Function | Implementation |
|-----------|----------|---------------|
| **Build Runner** | 编译 workload + stress | 调用 cmake + make, 支持交叉编译 |
| **Deploy Runner** | 部署到目标机器 | 用户提供部署脚本, Harness 调用 |
| **Execution Runner** | 执行 workload + stress | 控制 warmup → 运行 → 停止 |
| **Metrics Collector** | 采集 Topdown/火焰图/内存 | devkit + perf record |
| **Profile Comparator** | 对比两个 Profile | 数值对比 + 热点覆盖率计算 |

### Deploy Configuration (User Provided)

Deploy 是用户必须提供的输入，因为不同 workload 的依赖软件栈差异很大：

```json
{
  "deploy_config": {
    "target_host": "192.168.1.100",
    "target_arch": "arm64",
    "os": "linux",
    "dependencies": [
      { "name": "folly", "version": "2.1.0" },
      { "name": "fbthrift", "version": "1.5.0" },
      { "name": "jemalloc", "version": "5.3.0" }
    ],
    "deploy_script": "deploy/search_ranking_deploy.sh",
    "env_vars": { "JEMALLOC_BG_THREAD": "1" },
    "ssh_key": "path/to/key"
  }
}
```

### Execution Flow with Warmup

```
启动 workload → warmup 期 (不采集, 默认 30s)
    → warmup 结束, 开始采集
    → 同时启动:
        a) devkit 采集 Topdown + 内存带宽
        b) perf record 采集火焰图
    → 采集期 (默认 60s)
    → 采集结束, 停止 workload
    → 解析采集数据 → 生成 workload Profile
```

```json
{
  "run_config": {
    "warmup_seconds": 30,
    "measurement_seconds": 60,
    "concurrency": 16,
    "qps": 1000,
    "ramp_up_seconds": 10
  }
}
```

### Code Validation Three Steps

```
Step 1: Build Validation
  build_workload() → 编译成功?
    → 失败: 返回编译错误给 Agent, Agent 修复代码, 最多重试 3 次

Step 2: Run Validation
  短时间运行 (5s) → 能启动? 有输出?
    → 常见问题: 端口冲突、权限不足、依赖缺失
    → 失败: 返回运行错误给 Agent, 最多重试 3 次

Step 3: Collection Validation
  短时间采集 → Topdown 数据合理?
    → 常见问题: 全是 retiring (太简单)、全是 frontend bound (编译选项不对)
    → 失败: 返回不合理数据给 Agent, Agent 调整 Behavior Profile
```

### Profile Comparison Report

```json
{
  "iteration": 3,
  "comparison": {
    "topdown_l1": {
      "frontend_bound": { "customer": 0.25, "workload": 0.22, "diff": -0.03, "diff_pct": -12.0, "within_threshold": false },
      "backend_bound": { "customer": 0.40, "workload": 0.38, "diff": -0.02, "diff_pct": -5.0, "within_threshold": true },
      "bad_speculation": { "customer": 0.10, "workload": 0.11, "diff": 0.01, "diff_pct": 10.0, "within_threshold": true },
      "retiring": { "customer": 0.25, "workload": 0.29, "diff": 0.04, "diff_pct": 16.0, "within_threshold": false }
    },
    "memory": {
      "bandwidth_gbps": { "customer": 45.2, "workload": 43.8, "diff": -1.4, "diff_pct": -3.1, "within_threshold": true }
    },
    "hotspot_coverage": {
      "total_open_source_hotspots": 50,
      "covered_in_workload": 42,
      "coverage_pct": 84.0,
      "within_threshold": true,
      "missed_hotspots": ["folly::detail::ThreadPool::dispatch", "brpc::Controller::onResponse"]
    },
    "convergence": {
      "converged": false,
      "reason": "frontend_bound diff_pct -12% and retiring diff_pct 16% exceed threshold 10%",
      "iteration_count": 3,
      "max_iterations": 10
    }
  },
  "recommendation": "Priority 2: 调 Behavior Profile — 增加 frontend bound 行为 (fetch latency), 减少 retiring 比重"
}
```

### Hotspot Coverage Calculation

```
For each hotspot function in customer flamegraph (self_pct > threshold, e.g. 1%):
  - If function source is "open_source":
    - If function appears in workload flamegraph → covered ✓
  - If function source is "customer_custom":
    - Skipped (无法复刻, 预期)

Coverage = open_source covered count / total open_source hotspot count
```

Only counts coverage at the open-source library layer — customer custom code is expected to be unreplicable.

---

## 7. Iteration Loop

### Full End-to-End Flow

```
User Input: 客户数据 + 部署配置
    → Data Ingestion: 解析为 Profile
    → Agent Phase 1: 宏观分析
        - 识别 Topdown 瓶颈类型
        - 提取热点函数列表, 分类开源/自研
        - 理解 Business Workflow 结构
        - 输出: Workflow 划分 + 各 Stage Behavior Profile
    → Agent Phase 2: 细节填充
        - 为每个 Stage 选择行为实现策略
        - 确定开源库调用方案
        - 输出: 完整生成指令
    → Code Gen Engine: 生成完整项目
    → Build Validation → 成功/失败 → 失败则 Agent 修复重试
    → Deploy (用户脚本) → 部署到目标机器
    → Run Validation → 短时间运行校验 → 失败则 Agent 诊断重试
    → Collection Validation → 短时间采集校验
    → Full Run: warmup + 采集期
    → Metrics Collector: 采集 Topdown + 火焰图 + 内存
    → Profile Comparator: 对比 → 差异报告
    → Agent 评估 → 是否收敛?
        → 否: 按优先级调整 (调参 > 调行为 > 调 Workflow > 调架构) → 循环
        → 是: 输出最终结果
```

### Convergence Conditions

| Metric | Threshold |
|--------|-----------|
| Topdown L1 四项误差 | < 10% |
| 内存带宽误差 | < 5% |
| 开源库热点覆盖率 | > 80% |

All three must be met simultaneously.

### Failure Handling

| Scenario | Response | Max Retries |
|----------|----------|-------------|
| 编译失败 | Agent 修复代码 | 3 |
| 运行失败 (启动/端口/权限) | Agent 诊断修复 | 3 |
| 采集数据不合理 | Agent 调整 Behavior Profile | 无限 (每迭代算一次) |
| 迭代超过 max_iterations (默认 10) | 输出最佳结果 + 未对齐指标说明 | - |

---

## 8. Project Structure

```
harness/
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-07-27-workload-simulation-design.md
├── src/
│   ├── ingestion/              # Data Ingestion parsers
│   │   ├── flamegraph_parser.py
│   │   ├── topdown_parser.py
│   │   ├── memory_parser.py
│   │   ├── version_parser.py
│   │   └── text_parser.py      # LLM-assisted
│   ├── profile/                # Profile Store
│   │   ├── profile_schema.py
│   │   ├── profile_store.py
│   │   └── comparator.py
│   ├── agent/                  # Agent Core
│   │   ├── agent_core.py       # Agent orchestrator
│   │   ├── mcp_server.py       # MCP tool server
│   │   ├── strategy.py         # Iteration strategy logic
│   │   └── prompts/            # Agent prompt templates
│   │       ├── analyze_profile.md
│   │       ├── plan_workflow.md
│   │       ├── detail_fill.md
│   │       ├── evaluate_comparison.md
│   │       └── fix_code.md
│   ├── codegen/                # Code Gen Engine
│   │   ├── scaffold_gen.py     # Layer 0: Project scaffold
│   │   ├── service_gen.py      # Layer 1: Service skeleton
│   │   ├── workflow_gen.py     # Layer 2: Business workflow
│   │   ├── behavior_gen.py     # Layer 3: Behavior profiles
│   │   ├── knob_gen.py         # Layer 4: Tuning knobs
│   │   ├── templates/          # C++ code templates
│   │   │   ├── cmake/
│   │   │   ├── service/
│   │   │   ├── stress/
│   │   │   └── behaviors/
│   │   │       ├── compute_synthesis.cpp
│   │   │       ├── memory_synthesis.cpp
│   │   │       ├── mixed_synthesis.cpp
│   │   │       └── direct_call_wrappers.cpp
│   │   └── validator.py        # Code validation
│   ├── harness/                # Execution Harness
│   │   ├── build_runner.py
│   │   ├── deploy_runner.py
│   │   ├── execution_runner.py
│   │   ├── metrics_collector.py
│   │   └── run_config.py
│   └── config/                 # Configuration
│       ├── default_config.yaml
│       └── deploy_schema.json
├── tests/
│   ├── ingestion/
│   ├── profile/
│   ├── agent/
│   ├── codegen/
│   └── harness/
├── examples/
│   ├── search_ranking/         # 搜推排序示例
│   │   ├── customer_data/
│   │   └── expected_output/
│   └── recommendation/         # 推荐示例
│       ├── customer_data/
│       └── expected_output/
└── README.md
```

---

## 9. Key Technical Decisions

### 9.1 ARM64 Topdown Methodology

采用 ARM 官方 Neoverse Topdown 方法论：
- Level 1: Frontend Bound / Backend Bound / Bad Speculation / Retiring
- Level 2: 进一步细分 (Memory Bound vs Core Bound, Fetch Latency vs Branch Detect 等)
- PMU counter 映射根据具体 Neoverse core (N1/V1/N2/V2) 不同
- 采集方式: devkit 工具 + `perf stat -M TopdownL1` / `perf stat -M TopdownL2`

参考: ARM Neoverse Topdown Analysis Method official documentation

### 9.2 Benchmark Cloning Methodology

借鉴学术前沿的 benchmark cloning 方法，但不同于传统方法：
- **传统方法**: 纯统计 instruction mix + cache miss rate 复刻
- **我们的方法**: 在开源库调用栈上对齐 + Behavior Profile 合成客户自研部分 + Topdown 目标驱动迭代

这种方法的优势：
- 火焰图上的开源库部分看起来与客户一致（调用栈路径对齐）
- 微架构特征通过 Topdown 目标验证（不只是 instruction mix）
- 增量迭代有明确目标（Topdown 四项误差 < 10%）

### 9.3 MCP as Agent-Harness Interface

MCP (Model Context Protocol) 作为 Agent 和 Harness 之间的标准接口：
- Harness 各组件暴露为 MCP tools
- Agent 通过 MCP protocol 调用工具
- Agent 选型可替换（Claude/GPT/opencode/自研），只要适配 MCP client

### 9.4 Language Choice

框架本身用 **Python** 实现（Data Ingestion + Harness orchestration），因为：
- 数据解析和对比逻辑用 Python 更方便
- MCP server 有成熟的 Python SDK
- Agent prompt 编排用 Python 更灵活

生成的 workload/stress 程序是 **C++**，因为：
- 客户目标软件是 C++
- 开源基础库（folly/fbthrift/brpc/taskflow）是 C++
- ARM64 Topdown 分析对 C++ workload 更直接

---

## 10. Phased Implementation Plan

### Phase 1: Minimum Viable Loop (2-3 weeks)

Goal: 端到端闭环跑通，哪怕对齐度很低

- Data Ingestion: FlamegraphParser + TopdownParser (最基础的两个)
- Profile Store: 简单 JSON 文件存储
- Agent Core: 用 Claude API + 简单 prompt 链 (不搞复杂编排)
- Code Gen Engine: 只生成单模块 standalone workload (不搞多模块)
- Harness: build + run + collect + compare (最基础流程)
- 无迭代循环, 手动驱动

### Phase 2: Auto Iteration (2-3 weeks)

Goal: 自动迭代闭环

- Agent Core: 完整 Agent orchestrator + MCP server
- Code Gen Engine: 支持 Behavior Profile + config.json 调参
- Harness: warmup + 自动采集 + Profile Comparator + 对比报告
- 迭代循环: 调参优先 → 调行为 → 调 Workflow

### Phase 3: Large Project Support (3-4 weeks)

Goal: 支持大项目（搜推编排引擎级别）

- Data Ingestion: 支持 Level 2 Topdown + 内存特征 + 优化策略 + 版本信息
- Code Gen Engine: 五层完整架构 + 多模块项目 + stress 程序
- Agent Core: 多阶段 Business Workflow 规划 + 复杂迭代策略
- Harness: 多组件部署 + 分布式采集

### Phase 4: Multi-Language & Scale (ongoing)

Goal: 扩展到 Go/Java/Python workload

- Code Gen Engine: Go/Java/Python 模板
- Data Ingestion: Go/Java/Python 相关的火焰图/Topdown 解析
- 其他业务场景覆盖

---

## 11. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Agent 生成代码质量不稳定 | 代码模板 + Agent 填充模式 + 三步校验 |
| 大项目调用栈复杂度高 | 先做 Workflow Stage 划分 + Behavior Profile 抽象，不是逐函数复刻 |
| ARM64 PMU counter 映射差异 | devkit 工具已处理，Profile Comparator 只看 Topdown 比例，不看 raw counter |
| 采集数据噪声大 | warmup 机制 + 多次采集取平均 |
| 客户数据格式不统一 | 解析器支持多种输入格式 + TextParser 用 LLM 处理非结构化输入 |
| 迭代收敛困难 | 优先级迭代策略 + 10 次上限 + 输出最佳结果 |

---

## References

- ARM Neoverse Topdown Analysis Method official documentation
- "Benchmark Cloning" research (ISPASS/MICRO/ASPLOS 2024-2025)
- MCP (Model Context Protocol) specification: https://modelcontextprotocol.io/
- SimPoint / Basic Block Vector methodology for workload characterization
- Brendan Gregg's Flamegraph tools and methodology
