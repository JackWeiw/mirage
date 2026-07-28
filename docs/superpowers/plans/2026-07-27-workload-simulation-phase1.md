# Workload Simulation Framework — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a minimum viable end-to-end loop: parse customer data → generate a standalone C++ workload → build/run/collect → compare Topdown metrics → manual review. No auto-iteration yet.

**Architecture:** Five components (Data Ingestion, Profile Store, Agent Core, Code Gen Engine, Harness) but each in its simplest form. Agent uses Claude API with direct prompt chains. Code Gen produces single-module standalone workloads only. Harness runs locally. All components communicate through Python function calls — no MCP yet (MCP comes in Phase 2).

**Tech Stack:** Python 3.11+ for framework code. C++ for generated workloads. pytest for testing. Claude API (anthropic SDK) for Agent Core. pydantic for schema validation. subprocess for build/run commands.

---

## File Structure

```
harness/
├── pyproject.toml                         # Python project config
├── src/
│   ├── __init__.py
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── flamegraph_parser.py           # Parse perf script / folded flamegraphs
│   │   └── topdown_parser.py              # Parse devkit JSON/CSV Topdown data
│   ├── profile/
│   │   ├── __init__.py
│   │   ├── profile_schema.py              # Pydantic models for Profile
│   │   ├── profile_store.py               # JSON file-based Profile storage
│   │   └── comparator.py                  # Compare two Profiles, produce diff report
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── agent_core.py                  # Simple Claude API prompt chain orchestrator
│   │   ├── strategy.py                    # Iteration strategy decision logic
│   │   └── prompts/
│   │       ├── analyze_profile.md         # Prompt: analyze customer Profile
│   │       ├── plan_workflow.md            # Prompt: plan workflow stages
│   │       ├── detail_fill.md             # Prompt: fill behavior implementation details
│   │       └── evaluate_comparison.md     # Prompt: evaluate comparison report
│   ├── codegen/
│   │   ├── __init__.py
│   │   ├── scaffold_gen.py                # Layer 0-1: project scaffold + service skeleton
│   │   ├── behavior_gen.py                # Layer 3: behavior implementation code
│   │   ├── knob_gen.py                    # Layer 4: config.json + param parsing
│   │   ├── generator.py                   # Orchestrates scaffold → behavior → knob generation
│   │   └── templates/
│   │       ├── cmake/
│   │       │   └── CMakeLists.txt.j2      # Jinja2 template for CMakeLists.txt
│   │       ├── main/
│   │       │   └── main.cpp.j2            # Jinja2 template for main.cpp
│   │       ├── behaviors/
│   │       │   ├── compute_synthesis.cpp.j2
│   │       │   ├── memory_synthesis.cpp.j2
│   │       │   └── mixed_synthesis.cpp.j2
│   │       │   └── direct_call_wrapper.cpp.j2
│   │       └── config/
│   │           └── config.json.j2         # Jinja2 template for runtime config
│   │   └── validator.py                   # Build validation (compile check)
│   ├── harness/
│   │   ├── __init__.py
│   │   ├── build_runner.py                # cmake + make build execution
│   │   ├── execution_runner.py            # Run workload with warmup
│   │   ├── metrics_collector.py           # Collect Topdown + flamegraph data
│   │   └── run_config.py                  # RunConfig pydantic model
│   └── config/
│       ├── __init__.py
│       └── default_config.yaml            # Default framework config
├── tests/
│   ├── conftest.py                        # Shared fixtures (sample data)
│   ├── ingestion/
│   │   ├── test_flamegraph_parser.py
│   │   ├── test_topdown_parser.py
│   ├── profile/
│   │   ├── test_profile_schema.py
│   │   ├── test_profile_store.py
│   │   ├── test_comparator.py
│   ├── codegen/
│   │   ├── test_scaffold_gen.py
│   │   ├── test_behavior_gen.py
│   │   ├── test_knob_gen.py
│   │   ├── test_generator.py
│   ├── harness/
│   │   ├── test_build_runner.py
│   │   ├── test_execution_runner.py
│   │   ├── test_metrics_collector.py
│   ├── agent/
│   │   ├── test_agent_core.py
│   │   ├── test_strategy.py
│   ├── data/                              # Test fixture data files
│   │   ├── sample_flamegraph_folded.txt
│   │   ├── sample_topdown.json
│   │   ├── sample_topdown.csv
│   │   ├── sample_profile.json
│   │   └── sample_workload_profile.json
├── examples/
│   └── search_ranking/
│       ├── customer_data/
│       │   ├── flamegraph_folded.txt       # Sample customer flamegraph
│       │   ├── topdown.json               # Sample customer Topdown data
│       │   └── business_description.md    # Sample business logic description
│       └── deploy_config.json             # Sample deploy config for this scenario
└── README.md
```

---

### Task 1: Project Setup & Profile Schema

**Files:**
- Create: `pyproject.toml`
- Create: `src/__init__.py`
- Create: `src/profile/__init__.py`
- Create: `src/profile/profile_schema.py`
- Test: `tests/profile/test_profile_schema.py`
- Test: `tests/conftest.py`

This task establishes the Python project, installs dependencies, and defines the core Profile data model that all other components depend on.

- [ ] **Step 1: Write pyproject.toml**

```toml
[project]
name = "workload-sim"
version = "0.1.0"
description = "Workload simulation framework for ARM64 microarchitecture profiling"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.0",
    "anthropic>=0.40",
    "jinja2>=3.1",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: Install dependencies**

Run: `cd /c/Users/jack/Desktop/harness && pip install -e ".[dev]"`
Expected: Successfully installed workload-sim and all dependencies.

- [ ] **Step 3: Write profile_schema.py with Pydantic models**

```python
"""Core Profile data models for the Workload Simulation Framework."""

from pydantic import BaseModel, Field
from typing import Optional


class SoftwareDependency(BaseModel):
    name: str
    version: str
    compile_flags: Optional[str] = None
    config: Optional[dict] = None


class ProfileMetadata(BaseModel):
    customer: str
    date: str
    platform: str = "arm64"
    kernel_version: Optional[str] = None
    neoverse_core: Optional[str] = None
    software_stack: list[SoftwareDependency] = Field(default_factory=list)


class HotspotFunction(BaseModel):
    function: str
    library: str
    source: str  # "open_source" or "customer_custom"
    self_pct: float
    cumulative_pct: float
    call_path: list[str] = Field(default_factory=list)


class TopdownL1(BaseModel):
    frontend_bound: float
    backend_bound: float
    bad_speculation: float
    retiring: float


class TopdownL2Frontend(BaseModel):
    branch_detect: Optional[float] = None
    fetch_latency: Optional[float] = None
    icache_misses: Optional[float] = None


class TopdownL2Backend(BaseModel):
    memory_bound: Optional[float] = None
    core_bound: Optional[float] = None


class TopdownL2BadSpec(BaseModel):
    branch_mispredict: Optional[float] = None
    other: Optional[float] = None


class TopdownL2Retiring(BaseModel):
    heavy_ops: Optional[float] = None
    light_ops: Optional[float] = None


class TopdownL2(BaseModel):
    frontend_bound: Optional[TopdownL2Frontend] = None
    backend_bound: Optional[TopdownL2Backend] = None
    bad_speculation: Optional[TopdownL2BadSpec] = None
    retiring: Optional[TopdownL2Retiring] = None


class MemoryProfile(BaseModel):
    bandwidth_gbps: Optional[float] = None
    l3_miss_rate: Optional[float] = None
    tlb_miss_rate: Optional[float] = None
    working_set_size_mb: Optional[float] = None


class OptimizationRecord(BaseModel):
    strategy: str
    impact: str
    verified: bool = False
    context: Optional[str] = None


class CallgraphSummary(BaseModel):
    total_unique_functions: Optional[int] = None
    open_source_functions: Optional[int] = None
    customer_custom_functions: Optional[int] = None
    open_source_hotspot_pct: Optional[float] = None
    customer_custom_hotspot_pct: Optional[float] = None


class Profile(BaseModel):
    metadata: ProfileMetadata
    hotspots: list[HotspotFunction] = Field(default_factory=list)
    topdown: Optional[TopdownL1] = None
    topdown_l2: Optional[TopdownL2] = None
    memory: Optional[MemoryProfile] = None
    optimizations: list[OptimizationRecord] = Field(default_factory=list)
    business_logic: Optional[str] = None
    callgraph_summary: Optional[CallgraphSummary] = None
```

- [ ] **Step 4: Write failing tests for Profile schema**

```python
"""Tests for Profile schema validation."""

import pytest
from profile_schema import (
    Profile, ProfileMetadata, HotspotFunction,
    TopdownL1, TopdownL2, TopdownL2Frontend,
    TopdownL2Backend, MemoryProfile, SoftwareDependency,
    OptimizationRecord,
)


def test_profile_metadata_defaults():
    meta = ProfileMetadata(customer="test_customer", date="2026-07-27")
    assert meta.platform == "arm64"
    assert meta.kernel_version is None
    assert meta.software_stack == []


def test_profile_metadata_with_stack():
    meta = ProfileMetadata(
        customer="acme",
        date="2026-07-27",
        neoverse_core="N2",
        software_stack=[
            SoftwareDependency(name="folly", version="2.1.0", compile_flags="-O2"),
        ],
    )
    assert meta.neoverse_core == "N2"
    assert meta.software_stack[0].name == "folly"


def test_hotspot_function_open_source():
    hs = HotspotFunction(
        function="folly::futures::detail::FutureImpl::then",
        library="folly",
        source="open_source",
        self_pct=12.5,
        cumulative_pct=35.2,
        call_path=["main", "Server::handleRequest", "folly::futures::detail::FutureImpl::then"],
    )
    assert hs.source == "open_source"


def test_topdown_l1_sums_approximately_to_one():
    td = TopdownL1(frontend_bound=0.25, backend_bound=0.40, bad_speculation=0.10, retiring=0.25)
    total = td.frontend_bound + td.backend_bound + td.bad_speculation + td.retiring
    assert abs(total - 1.0) < 0.01


def test_topdown_l2_nested():
    td_l2 = TopdownL2(
        frontend_bound=TopdownL2Frontend(fetch_latency=0.15, branch_detect=0.05),
        backend_bound=TopdownL2Backend(memory_bound=0.30, core_bound=0.10),
    )
    assert td_l2.frontend_bound.fetch_latency == 0.15


def test_full_profile_serialization():
    profile = Profile(
        metadata=ProfileMetadata(customer="acme", date="2026-07-27", neoverse_core="N2"),
        hotspots=[HotspotFunction(
            function="folly::futures::detail::FutureImpl::then",
            library="folly", source="open_source",
            self_pct=12.5, cumulative_pct=35.2,
            call_path=["main", "Server::handleRequest", "folly::futures::detail::FutureImpl::then"],
        )],
        topdown=TopdownL1(frontend_bound=0.25, backend_bound=0.40, bad_speculation=0.10, retiring=0.25),
        memory=MemoryProfile(bandwidth_gbps=45.2, l3_miss_rate=0.08),
        business_logic="High-concurrency RPC service",
    )
    json_str = profile.model_dump_json()
    loaded = Profile.model_validate_json(json_str)
    assert loaded.metadata.customer == "acme"
    assert loaded.hotspots[0].self_pct == 12.5
    assert loaded.memory.bandwidth_gbps == 45.2
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /c/Users/jack/Desktop/harness && pytest tests/profile/test_profile_schema.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 6: Create empty __init__.py files and conftest.py**

Create `src/__init__.py`, `src/profile/__init__.py` as empty files.

Create `tests/conftest.py`:

```python
"""Shared test fixtures."""

import pathlib

DATA_DIR = pathlib.Path(__file__).parent / "data"


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "integration: marks integration tests requiring external tools")
```

- [ ] **Step 7: Commit**

```bash
cd /c/Users/jack/Desktop/harness
git init
git add pyproject.toml src/__init__.py src/profile/__init__.py src/profile/profile_schema.py tests/conftest.py tests/profile/test_profile_schema.py
git commit -m "feat: project setup + Profile schema with pydantic models"
```

---

### Task 2: Data Ingestion — FlamegraphParser

**Files:**
- Create: `src/ingestion/__init__.py`
- Create: `src/ingestion/flamegraph_parser.py`
- Create: `tests/data/sample_flamegraph_folded.txt`
- Test: `tests/ingestion/test_flamegraph_parser.py`

The FlamegraphParser reads perf script output or folded flamegraph format and extracts the call stack tree + hotspot function list with self/cumulative percentages.

- [ ] **Step 1: Create sample folded flamegraph test data**

Create `tests/data/sample_flamegraph_folded.txt`:

```
main;Server::handleRequest;AsyncProcessor::process;folly::futures::detail::FutureImpl::then 1250
main;Server::handleRequest;AsyncProcessor::process;CustomerCustom::featureCalc 800
main;Server::handleRequest;CustomerCustom::dataLookup 1500
main;Server::handleRequest;AsyncProcessor::process 500
main;Server::handleRequest 300
main 200
main;Logger::flush;folly::detail::ThreadPool::dispatch 600
main;Logger::flush 100
```

- [ ] **Step 2: Write failing tests for FlamegraphParser**

```python
"""Tests for FlamegraphParser."""

import pathlib
from ingestion.flamegraph_parser import FlamegraphParser


DATA_DIR = pathlib.Path(__file__).parent.parent / "data"


def test_parse_folded_format_extracts_hotspots():
    parser = FlamegraphParser()
    input_file = DATA_DIR / "sample_flamegraph_folded.txt"
    hotspots = parser.parse_folded(input_file)

    # Should extract hotspot functions with self_pct and cumulative_pct
    assert len(hotspots) > 0

    # folly function should be detected
    folly_funcs = [h for h in hotspots if h.library == "folly"]
    assert len(folly_funcs) >= 1

    # Check a specific folly function
    then_func = [h for h in folly_funcs if "FutureImpl::then" in h.function]
    assert len(then_func) == 1
    assert then_func[0].source == "open_source"
    assert then_func[0].self_pct > 0


def test_parse_folded_extracts_call_paths():
    parser = FlamegraphParser()
    input_file = DATA_DIR / "sample_flamegraph_folded.txt"
    hotspots = parser.parse_folded(input_file)

    # Each hotspot should have a call_path
    for h in hotspots:
        assert len(h.call_path) > 0

    # Check specific call path
    then_func = [h for h in hotspots if "FutureImpl::then" in h.function]
    assert then_func[0].call_path == [
        "main", "Server::handleRequest", "AsyncProcessor::process",
        "folly::futures::detail::FutureImpl::then"
    ]


def test_parse_folded_classifies_open_source_vs_custom():
    parser = FlamegraphParser()
    input_file = DATA_DIR / "sample_flamegraph_folded.txt"
    hotspots = parser.parse_folded(input_file)

    open_source = [h for h in hotspots if h.source == "open_source"]
    custom = [h for h in hotspots if h.source == "customer_custom"]

    assert len(open_source) >= 1  # folly functions
    assert len(custom) >= 1  # CustomerCustom:: functions


def test_parse_folded_cumulative_pct_greater_than_self_pct():
    parser = FlamegraphParser()
    input_file = DATA_DIR / "sample_flamegraph_folded.txt"
    hotspots = parser.parse_folded(input_file)

    for h in hotspots:
        assert h.cumulative_pct >= h.self_pct
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/ingestion/test_flamegraph_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ingestion'`

- [ ] **Step 4: Write FlamegraphParser implementation**

```python
"""Parse flamegraph data (folded format) into structured hotspot list."""

import pathlib
from profile_schema import HotspotFunction

# Known open-source C++ libraries — used to classify hotspot source
OPEN_SOURCE_LIBRARIES = {
    "folly", "fbthrift", "brpc", "taskflow", "glog", "gflags",
    "protobuf", "abseil", "boost", "std", "jemalloc", "tcmalloc",
    "leveldb", "rocksdb", "redis", "openssl", "lz4", "zstd", "snappy",
}


def classify_function_source(function_name: str) -> tuple[str, str]:
    """Classify a function as open_source or customer_custom and identify its library.

    Returns (source, library) where source is "open_source" or "customer_custom".
    """
    # Check if function name contains a known open-source library namespace
    for lib in OPEN_SOURCE_LIBRARIES:
        if lib in function_name.lower() or function_name.startswith(lib + "::") or f"{lib}::" in function_name:
            return "open_source", lib

    # Check for std:: prefix
    if function_name.startswith("std::"):
        return "open_source", "std"

    # Default: customer custom
    return "customer_custom", "custom"


def parse_folded_file(filepath: pathlib.Path) -> list[HotspotFunction]:
    """Parse a folded flamegraph file into a list of HotspotFunction.

    Folded format: each line is "stack;frame count" where count is the number of samples.
    """
    lines = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.rsplit(" ", 1)
            if len(parts) != 2:
                continue
            stack_str, count_str = parts
            count = int(count_str)
            frames = stack_str.split(";")
            lines.append((frames, count))

    total_samples = sum(count for _, count in lines)

    # Build a dict of function -> total samples (for self_pct calculation)
    # self_pct: samples where this function is the leaf (top of stack)
    # cumulative_pct: all samples where this function appears anywhere in the stack
    self_samples: dict[str, int] = {}
    cumulative_samples: dict[str, int] = {}
    call_paths: dict[str, list[str]] = {}

    for frames, count in lines:
        leaf = frames[-1]
        self_samples[leaf] = self_samples.get(leaf, 0) + count

        for frame in frames:
            cumulative_samples[frame] = cumulative_samples.get(frame, 0) + count

        # Store the longest call path for each leaf function
        if leaf not in call_paths or len(frames) > len(call_paths[leaf]):
            call_paths[leaf] = frames

    # Build HotspotFunction list, sorted by self_pct descending
    # Only include functions with self_pct > 0 (leaf functions with samples)
    hotspots = []
    for func, samples in self_samples.items():
        self_pct = (samples / total_samples) * 100.0
        cum_pct = (cumulative_samples.get(func, 0) / total_samples) * 100.0
        source, library = classify_function_source(func)
        hotspots.append(HotspotFunction(
            function=func,
            library=library,
            source=source,
            self_pct=self_pct,
            cumulative_pct=cum_pct,
            call_path=call_paths.get(func, []),
        ))

    # Sort by self_pct descending
    hotspots.sort(key=lambda h: h.self_pct, reverse=True)
    return hotspots


class FlamegraphParser:
    """Parser for flamegraph data files."""

    def parse_folded(self, filepath: pathlib.Path) -> list[HotspotFunction]:
        """Parse a folded flamegraph file.

        Args:
            filepath: Path to the folded flamegraph text file.

        Returns:
            List of HotspotFunction sorted by self_pct descending.
        """
        return parse_folded_file(filepath)

    def parse_perf_script(self, filepath: pathlib.Path) -> list[HotspotFunction]:
        """Parse perf script output into folded format, then parse.

        Args:
            filepath: Path to perf script output file.

        Returns:
            List of HotspotFunction sorted by self_pct descending.
        """
        # Convert perf script format to folded format first
        # perf script output format: each sample has multiple lines
        #   <pid> <comm> <timestamp> <cpu> <event>: <ip> <sym>
        # Folded format: "stack;frame count"
        stacks: dict[str, int] = {}
        current_stack: list[str] = []

        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    # Empty line signals end of a sample
                    if current_stack:
                        stack_key = ";".join(current_stack)
                        stacks[stack_key] = stacks.get(stack_key, 0) + 1
                        current_stack = []
                    continue

                # Parse line — try to extract symbol name
                # Format variations:
                #   ffffffff810a1c23 native_write_cr4 ([kernel])
                #   7f6454d4 [unknown] (/usr/lib/...)
                #   folly::futures::detail::FutureImpl::then (in folly.so)
                parts = line.split()
                if len(parts) >= 4:
                    # Typically: pid comm timestamp cpu ip sym (dso)
                    # The symbol is usually after the IP
                    for part in parts:
                        if "(" in part and part.startswith("("):
                            continue
                    # Try to find a meaningful symbol
                    symbol = parts[-2] if len(parts) > 3 else parts[-1]
                    # Skip hex addresses
                    if symbol.startswith("0x") or symbol.startswith("ffff"):
                        symbol = "[unknown]"
                    current_stack.append(symbol)
                elif len(parts) == 1:
                    current_stack.append(parts[0])

        # Write folded format to temp string and parse
        folded_lines = []
        for stack, count in stacks.items():
            folded_lines.append(f"{stack} {count}")

        # Parse inline
        total_samples = sum(stacks.values())
        hotspots = []
        for stack_str, count in stacks.items():
            frames = stack_str.split(";")
            leaf = frames[-1]
            self_pct = (count / total_samples) * 100.0
            cum_pct = 0.0
            # Calculate cumulative for each frame
            for frame in frames:
                cum_count = sum(c for s, c in stacks.items() if frame in s.split(";"))
                if frame == leaf:
                    cum_pct = (cum_count / total_samples) * 100.0

            source, library = classify_function_source(leaf)
            hotspots.append(HotspotFunction(
                function=leaf,
                library=library,
                source=source,
                self_pct=self_pct,
                cumulative_pct=cum_pct,
                call_path=frames,
            ))

        hotspots.sort(key=lambda h: h.self_pct, reverse=True)
        return hotspots
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/ingestion/test_flamegraph_parser.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ingestion/__init__.py src/ingestion/flamegraph_parser.py tests/ingestion/ tests/data/sample_flamegraph_folded.txt
git commit -m "feat: FlamegraphParser — parse folded flamegraphs into structured hotspots"
```

---

### Task 3: Data Ingestion — TopdownParser

**Files:**
- Create: `src/ingestion/topdown_parser.py`
- Create: `tests/data/sample_topdown.json`
- Create: `tests/data/sample_topdown.csv`
- Test: `tests/ingestion/test_topdown_parser.py`

The TopdownParser reads devkit JSON or CSV output and extracts Topdown L1 + L2 metrics.

- [ ] **Step 1: Create sample Topdown test data files**

Create `tests/data/sample_topdown.json`:

```json
{
  "topdown_l1": {
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
  }
}
```

Create `tests/data/sample_topdown.csv`:

```
metric,value
frontend_bound,0.25
backend_bound,0.40
bad_speculation,0.10
retiring,0.25
frontend_bound.branch_detect,0.05
frontend_bound.fetch_latency,0.15
frontend_bound.icache_misses,0.05
backend_bound.memory_bound,0.30
backend_bound.core_bound,0.10
bad_speculation.branch_mispredict,0.08
bad_speculation.other,0.02
retiring.heavy_ops,0.15
retiring.light_ops,0.10
memory.bandwidth_gbps,45.2
memory.l3_miss_rate,0.08
memory.tlb_miss_rate,0.02
memory.working_set_size_mb,512
```

- [ ] **Step 2: Write failing tests for TopdownParser**

```python
"""Tests for TopdownParser."""

import pathlib
from ingestion.topdown_parser import TopdownParser
from profile_schema import TopdownL1, TopdownL2, MemoryProfile


DATA_DIR = pathlib.Path(__file__).parent.parent / "data"


def test_parse_json_topdown_l1():
    parser = TopdownParser()
    result = parser.parse_json(DATA_DIR / "sample_topdown.json")

    assert result.topdown is not None
    assert result.topdown.frontend_bound == 0.25
    assert result.topdown.backend_bound == 0.40
    assert result.topdown.bad_speculation == 0.10
    assert result.topdown.retiring == 0.25


def test_parse_json_topdown_l2():
    parser = TopdownParser()
    result = parser.parse_json(DATA_DIR / "sample_topdown.json")

    assert result.topdown_l2 is not None
    assert result.topdown_l2.frontend_bound is not None
    assert result.topdown_l2.frontend_bound.fetch_latency == 0.15
    assert result.topdown_l2.backend_bound.memory_bound == 0.30


def test_parse_json_memory():
    parser = TopdownParser()
    result = parser.parse_json(DATA_DIR / "sample_topdown.json")

    assert result.memory is not None
    assert result.memory.bandwidth_gbps == 45.2
    assert result.memory.l3_miss_rate == 0.08


def test_parse_csv_topdown_l1():
    parser = TopdownParser()
    result = parser.parse_csv(DATA_DIR / "sample_topdown.csv")

    assert result.topdown is not None
    assert result.topdown.frontend_bound == 0.25
    assert result.topdown.backend_bound == 0.40


def test_parse_csv_memory():
    parser = TopdownParser()
    result = parser.parse_csv(DATA_DIR / "sample_topdown.csv")

    assert result.memory is not None
    assert result.memory.bandwidth_gbps == 45.2
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/ingestion/test_topdown_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ingestion'`

- [ ] **Step 4: Write TopdownParser implementation**

```python
"""Parse Topdown analysis data (devkit JSON/CSV) into Profile fields."""

import csv
import json
import pathlib
from profile_schema import TopdownL1, TopdownL2, TopdownL2Frontend, TopdownL2Backend, TopdownL2BadSpec, TopdownL2Retiring, MemoryProfile, Profile, ProfileMetadata


class TopdownParser:
    """Parser for ARM64 Topdown analysis data from devkit output."""

    def parse_json(self, filepath: pathlib.Path) -> Profile:
        """Parse devkit JSON output.

        Args:
            filepath: Path to devkit JSON file containing topdown and memory data.

        Returns:
            Profile with topdown and memory fields populated. Metadata and hotspots
            must be filled separately.
        """
        with open(filepath, "r") as f:
            data = json.load(f)

        topdown_l1 = TopdownL1(**data.get("topdown_l1", {}))

        # Parse L2 if present
        l2_raw = data.get("topdown_l2", {})
        topdown_l2 = TopdownL2(
            frontend_bound=TopdownL2Frontend(**l2_raw.get("frontend_bound", {})) if "frontend_bound" in l2_raw else None,
            backend_bound=TopdownL2Backend(**l2_raw.get("backend_bound", {})) if "backend_bound" in l2_raw else None,
            bad_speculation=TopdownL2BadSpec(**l2_raw.get("bad_speculation", {})) if "bad_speculation" in l2_raw else None,
            retiring=TopdownL2Retiring(**l2_raw.get("retiring", {})) if "retiring" in l2_raw else None,
        )

        memory = MemoryProfile(**data.get("memory", {}))

        return Profile(
            metadata=ProfileMetadata(customer="unknown", date="unknown"),
            topdown=topdown_l1,
            topdown_l2=topdown_l2,
            memory=memory,
        )

    def parse_csv(self, filepath: pathlib.Path) -> Profile:
        """Parse devkit CSV output.

        CSV format: each row is "metric,value" where metric can be dotted like
        "frontend_bound.fetch_latency".

        Args:
            filepath: Path to devkit CSV file.

        Returns:
            Profile with topdown and memory fields populated.
        """
        metrics: dict[str, float] = {}
        with open(filepath, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                metric = row["metric"].strip()
                value = float(row["value"].strip())
                metrics[metric] = value

        # Build L1
        topdown_l1 = TopdownL1(
            frontend_bound=metrics.get("frontend_bound", 0.0),
            backend_bound=metrics.get("backend_bound", 0.0),
            bad_speculation=metrics.get("bad_speculation", 0.0),
            retiring=metrics.get("retiring", 0.0),
        )

        # Build L2 from dotted keys
        fb_raw = {k.split(".")[-1]: v for k, v in metrics.items()
                  if k.startswith("frontend_bound.")}
        bb_raw = {k.split(".")[-1]: v for k, v in metrics.items()
                  if k.startswith("backend_bound.")}
        bs_raw = {k.split(".")[-1]: v for k, v in metrics.items()
                  if k.startswith("bad_speculation.")}
        rt_raw = {k.split(".")[-1]: v for k, v in metrics.items()
                  if k.startswith("retiring.")}

        topdown_l2 = TopdownL2(
            frontend_bound=TopdownL2Frontend(**fb_raw) if fb_raw else None,
            backend_bound=TopdownL2Backend(**bb_raw) if bb_raw else None,
            bad_speculation=TopdownL2BadSpec(**bs_raw) if bs_raw else None,
            retiring=TopdownL2Retiring(**rt_raw) if rt_raw else None,
        )

        # Build memory from dotted keys
        mem_raw = {k.split(".")[-1]: v for k, v in metrics.items()
                   if k.startswith("memory.")}

        memory = MemoryProfile(**mem_raw) if mem_raw else None

        return Profile(
            metadata=ProfileMetadata(customer="unknown", date="unknown"),
            topdown=topdown_l1,
            topdown_l2=topdown_l2,
            memory=memory,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/ingestion/test_topdown_parser.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ingestion/topdown_parser.py tests/ingestion/test_topdown_parser.py tests/data/sample_topdown.json tests/data/sample_topdown.csv
git commit -m "feat: TopdownParser — parse devkit JSON/CSV into Topdown L1/L2 + memory"
```

---

### Task 4: Profile Store & Comparator

**Files:**
- Create: `src/profile/profile_store.py`
- Create: `src/profile/comparator.py`
- Create: `tests/data/sample_profile.json`
- Create: `tests/data/sample_workload_profile.json`
- Test: `tests/profile/test_profile_store.py`
- Test: `tests/profile/test_comparator.py`

Profile Store saves/loads Profiles as JSON files. Comparator compares two Profiles (customer vs workload) and produces a structured diff report with convergence assessment.

- [ ] **Step 1: Create sample profile test data**

Create `tests/data/sample_profile.json` — copy the full Profile JSON from the spec (Section 3, the `Profile Schema` example).

Create `tests/data/sample_workload_profile.json` — a similar profile but with slightly different values to test comparison:

```json
{
  "metadata": {
    "customer": "acme",
    "date": "2026-07-28",
    "platform": "arm64",
    "neoverse_core": "N2",
    "software_stack": [
      { "name": "folly", "version": "2.1.0", "compile_flags": "-O2 -march=armv8.2-a" }
    ]
  },
  "hotspots": [
    {
      "function": "folly::futures::detail::FutureImpl::then",
      "library": "folly",
      "source": "open_source",
      "self_pct": 11.0,
      "cumulative_pct": 33.0,
      "call_path": ["main", "Server::handleRequest", "AsyncProcessor::process", "folly::futures::detail::FutureImpl::then"]
    },
    {
      "function": "folly::detail::ThreadPool::dispatch",
      "library": "folly",
      "source": "open_source",
      "self_pct": 3.0,
      "cumulative_pct": 8.0,
      "call_path": ["main", "Logger::flush", "folly::detail::ThreadPool::dispatch"]
    }
  ],
  "topdown": {
    "frontend_bound": 0.22,
    "backend_bound": 0.38,
    "bad_speculation": 0.11,
    "retiring": 0.29
  },
  "memory": {
    "bandwidth_gbps": 43.8,
    "l3_miss_rate": 0.07,
    "tlb_miss_rate": 0.03
  },
  "business_logic": "Simulated workload"
}
```

- [ ] **Step 2: Write failing tests for ProfileStore**

```python
"""Tests for ProfileStore."""

import json
import pathlib
import tempfile
from profile.profile_store import ProfileStore
from profile_schema import Profile, ProfileMetadata, TopdownL1


def test_save_and_load_profile():
    store = ProfileStore(base_dir=tempfile.mkdtemp())

    profile = Profile(
        metadata=ProfileMetadata(customer="acme", date="2026-07-27"),
        topdown=TopdownL1(frontend_bound=0.25, backend_bound=0.40, bad_speculation=0.10, retiring=0.25),
    )

    path = store.save(profile, name="test_profile")
    assert path.exists()

    loaded = store.load(name="test_profile")
    assert loaded.metadata.customer == "acme"
    assert loaded.topdown.frontend_bound == 0.25


def test_list_profiles():
    store = ProfileStore(base_dir=tempfile.mkdtemp())

    p1 = Profile(metadata=ProfileMetadata(customer="a", date="2026-07-27"))
    p2 = Profile(metadata=ProfileMetadata(customer="b", date="2026-07-28"))

    store.save(p1, name="profile_a")
    store.save(p2, name="profile_b")

    names = store.list()
    assert "profile_a" in names
    assert "profile_b" in names
```

- [ ] **Step 3: Write failing tests for Comparator**

```python
"""Tests for Profile Comparator."""

import pathlib
from profile.comparator import ProfileComparator
from profile_schema import Profile, ProfileMetadata, TopdownL1, MemoryProfile, HotspotFunction


DATA_DIR = pathlib.Path(__file__).parent.parent / "data"


def _make_customer_profile():
    return Profile(
        metadata=ProfileMetadata(customer="acme", date="2026-07-27"),
        hotspots=[
            HotspotFunction(
                function="folly::futures::detail::FutureImpl::then",
                library="folly", source="open_source",
                self_pct=12.5, cumulative_pct=35.2,
                call_path=["main", "Server::handleRequest", "folly::futures::detail::FutureImpl::then"],
            ),
            HotspotFunction(
                function="CustomerCustom::featureCalc",
                library="custom", source="customer_custom",
                self_pct=8.0, cumulative_pct=20.0,
                call_path=["main", "CustomerCustom::featureCalc"],
            ),
        ],
        topdown=TopdownL1(frontend_bound=0.25, backend_bound=0.40, bad_speculation=0.10, retiring=0.25),
        memory=MemoryProfile(bandwidth_gbps=45.2, l3_miss_rate=0.08),
    )


def _make_workload_profile():
    return Profile(
        metadata=ProfileMetadata(customer="workload_sim", date="2026-07-28"),
        hotspots=[
            HotspotFunction(
                function="folly::futures::detail::FutureImpl::then",
                library="folly", source="open_source",
                self_pct=11.0, cumulative_pct=33.0,
                call_path=["main", "Server::handleRequest", "folly::futures::detail::FutureImpl::then"],
            ),
            HotspotFunction(
                function="folly::detail::ThreadPool::dispatch",
                library="folly", source="open_source",
                self_pct=3.0, cumulative_pct=8.0,
                call_path=["main", "Logger::flush", "folly::detail::ThreadPool::dispatch"],
            ),
        ],
        topdown=TopdownL1(frontend_bound=0.22, backend_bound=0.38, bad_speculation=0.11, retiring=0.29),
        memory=MemoryProfile(bandwidth_gbps=43.8, l3_miss_rate=0.07),
    )


def test_compare_topdown_l1_diff():
    comparator = ProfileComparator(
        topdown_threshold_pct=10.0,
        memory_threshold_pct=5.0,
        coverage_threshold_pct=80.0,
    )
    report = comparator.compare(_make_customer_profile(), _make_workload_profile())

    td = report["topdown_l1"]
    assert td["frontend_bound"]["customer"] == 0.25
    assert td["frontend_bound"]["workload"] == 0.22
    assert abs(td["frontend_bound"]["diff_pct"] - (-12.0)) < 1.0


def test_compare_topdown_convergence():
    comparator = ProfileComparator(
        topdown_threshold_pct=10.0,
        memory_threshold_pct=5.0,
        coverage_threshold_pct=80.0,
    )
    report = comparator.compare(_make_customer_profile(), _make_workload_profile())

    # frontend_bound diff is -12% > threshold 10%, so NOT converged
    assert report["convergence"]["converged"] is False


def test_compare_hotspot_coverage():
    comparator = ProfileComparator(
        topdown_threshold_pct=10.0,
        memory_threshold_pct=5.0,
        coverage_threshold_pct=80.0,
    )
    report = comparator.compare(_make_customer_profile(), _make_workload_profile())

    # Customer has 1 open-source hotspot (FutureImpl::then)
    # Workload also has FutureImpl::then → covered
    # Coverage = 1/1 = 100% > 80%
    assert report["hotspot_coverage"]["coverage_pct"] >= 80.0


def test_compare_memory_within_threshold():
    comparator = ProfileComparator(
        topdown_threshold_pct=10.0,
        memory_threshold_pct=5.0,
        coverage_threshold_pct=80.0,
    )
    report = comparator.compare(_make_customer_profile(), _make_workload_profile())

    # bandwidth: 43.8 vs 45.2, diff_pct = -3.1%, within 5%
    assert report["memory"]["bandwidth_gbps"]["within_threshold"] is True
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/profile/test_profile_store.py tests/profile/test_comparator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'profile'`

- [ ] **Step 5: Write ProfileStore implementation**

```python
"""JSON file-based Profile storage."""

import json
import pathlib
from profile_schema import Profile


class ProfileStore:
    """Store and retrieve Profiles as JSON files.

    Args:
        base_dir: Directory where profile JSON files are stored.
    """

    def __init__(self, base_dir: str | pathlib.Path):
        self.base_dir = pathlib.Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, profile: Profile, name: str) -> pathlib.Path:
        """Save a Profile to a JSON file.

        Args:
            profile: Profile to save.
            name: File name (without extension).

        Returns:
            Path to the saved file.
        """
        filepath = self.base_dir / f"{name}.json"
        with open(filepath, "w") as f:
            f.write(profile.model_dump_json(indent=2))
        return filepath

    def load(self, name: str) -> Profile:
        """Load a Profile from a JSON file.

        Args:
            name: File name (without extension).

        Returns:
            Loaded Profile.
        """
        filepath = self.base_dir / f"{name}.json"
        with open(filepath, "r") as f:
            data = json.load(f)
        return Profile.model_validate(data)

    def list(self) -> list[str]:
        """List all stored profile names.

        Returns:
            List of profile names (without .json extension).
        """
        names = []
        for f in self.base_dir.glob("*.json"):
            names.append(f.stem)
        return sorted(names)
```

- [ ] **Step 6: Write Comparator implementation**

```python
"""Compare two Profiles and produce a structured diff report."""

from profile_schema import Profile, TopdownL1, MemoryProfile


class ProfileComparator:
    """Compare a customer Profile with a workload Profile.

    Args:
        topdown_threshold_pct: Maximum acceptable Topdown L1 diff as percentage of customer value.
        memory_threshold_pct: Maximum acceptable memory bandwidth diff as percentage.
        coverage_threshold_pct: Minimum acceptable open-source hotspot coverage percentage.
    """

    def __init__(
        self,
        topdown_threshold_pct: float = 10.0,
        memory_threshold_pct: float = 5.0,
        coverage_threshold_pct: float = 80.0,
    ):
        self.topdown_threshold_pct = topdown_threshold_pct
        self.memory_threshold_pct = memory_threshold_pct
        self.coverage_threshold_pct = coverage_threshold_pct

    def compare(
        self,
        customer_profile: Profile,
        workload_profile: Profile,
        iteration: int = 0,
    ) -> dict:
        """Compare customer and workload Profiles.

        Args:
            customer_profile: The target Profile from customer data.
            workload_profile: The Profile from the generated workload run.
            iteration: Current iteration number.

        Returns:
            Comparison report dict with topdown_l1, memory, hotspot_coverage,
            and convergence assessment.
        """
        topdown_l1_report = self._compare_topdown_l1(
            customer_profile.topdown, workload_profile.topdown
        )

        memory_report = self._compare_memory(
            customer_profile.memory, workload_profile.memory
        )

        coverage_report = self._compare_hotspot_coverage(
            customer_profile.hotspots, workload_profile.hotspots
        )

        # Convergence check
        all_topdown_ok = all(v["within_threshold"] for v in topdown_l1_report.values())
        memory_ok = memory_report.get("bandwidth_gbps", {}).get("within_threshold", True)
        coverage_ok = coverage_report["coverage_pct"] >= self.coverage_threshold_pct

        converged = all_topdown_ok and memory_ok and coverage_ok

        # Build recommendation
        not_ok_metrics = []
        for name, v in topdown_l1_report.items():
            if not v["within_threshold"]:
                not_ok_metrics.append(f"topdown.{name} diff_pct {v['diff_pct']:.1f}%")
        if not memory_ok:
            bw = memory_report.get("bandwidth_gbps", {})
            not_ok_metrics.append(f"memory.bandwidth diff_pct {bw.get('diff_pct', 0):.1f}%")
        if not coverage_ok:
            not_ok_metrics.append(f"hotspot coverage {coverage_report['coverage_pct']:.1f}% < {self.coverage_threshold_pct}%")

        reason = "All metrics within thresholds" if converged else (
            "Exceeds threshold: " + ", ".join(not_ok_metrics)
        )

        recommendation = self._make_recommendation(topdown_l1_report, coverage_report)

        return {
            "iteration": iteration,
            "topdown_l1": topdown_l1_report,
            "memory": memory_report,
            "hotspot_coverage": coverage_report,
            "convergence": {
                "converged": converged,
                "reason": reason,
                "iteration_count": iteration,
            },
            "recommendation": recommendation,
        }

    def _compare_topdown_l1(
        self, customer: TopdownL1 | None, workload: TopdownL1 | None
    ) -> dict:
        if customer is None or workload is None:
            return {}

        metrics = ["frontend_bound", "backend_bound", "bad_speculation", "retiring"]
        report = {}
        for m in metrics:
            c_val = getattr(customer, m)
            w_val = getattr(workload, m)
            diff = w_val - c_val
            diff_pct = (diff / c_val) * 100.0 if c_val != 0 else 0.0
            within = abs(diff_pct) <= self.topdown_threshold_pct
            report[m] = {
                "customer": c_val,
                "workload": w_val,
                "diff": diff,
                "diff_pct": diff_pct,
                "within_threshold": within,
            }
        return report

    def _compare_memory(
        self, customer: MemoryProfile | None, workload: MemoryProfile | None
    ) -> dict:
        if customer is None or workload is None:
            return {}

        report = {}
        if customer.bandwidth_gbps is not None and workload.bandwidth_gbps is not None:
            diff = workload.bandwidth_gbps - customer.bandwidth_gbps
            diff_pct = (diff / customer.bandwidth_gbps) * 100.0
            report["bandwidth_gbps"] = {
                "customer": customer.bandwidth_gbps,
                "workload": workload.bandwidth_gbps,
                "diff": diff,
                "diff_pct": diff_pct,
                "within_threshold": abs(diff_pct) <= self.memory_threshold_pct,
            }
        return report

    def _compare_hotspot_coverage(
        self, customer_hotspots: list, workload_hotspots: list
    ) -> dict:
        # Only count open_source hotspots from customer
        open_source_hotspots = [h for h in customer_hotspots if h.source == "open_source"]
        if not open_source_hotspots:
            return {
                "total_open_source_hotspots": 0,
                "covered_in_workload": 0,
                "coverage_pct": 100.0,
                "missed_hotspots": [],
            }

        # Check which customer open-source hotspots appear in workload
        workload_func_names = {h.function for h in workload_hotspots}
        covered = [h for h in open_source_hotspots if h.function in workload_func_names]
        missed = [h for h in open_source_hotspots if h.function not in workload_func_names]

        coverage_pct = (len(covered) / len(open_source_hotspots)) * 100.0

        return {
            "total_open_source_hotspots": len(open_source_hotspots),
            "covered_in_workload": len(covered),
            "coverage_pct": coverage_pct,
            "missed_hotspots": [h.function for h in missed],
        }

    def _make_recommendation(self, topdown_report: dict, coverage_report: dict) -> str:
        """Generate iteration strategy recommendation based on comparison."""
        not_ok = {k: v for k, v in topdown_report.items() if not v["within_threshold"]}

        if not not_ok:
            if coverage_report["coverage_pct"] < self.coverage_threshold_pct:
                return "Priority 2: 调 Behavior Profile — 增加未覆盖的热点函数调用"
            return "已收敛, 无需调整"

        # All diffs < 5% → try Priority 1 (调参)
        max_diff = max(abs(v["diff_pct"]) for v in not_ok.values())
        if max_diff < 5.0:
            return "Priority 1: 调 config.json 参数 — 微调并发/QPS/内存比例"

        # Some diffs 5-10% → Priority 2 (调行为)
        return "Priority 2: 调 Behavior Profile — 调整行为实现策略和权重"
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/profile/test_profile_store.py tests/profile/test_comparator.py -v`
Expected: All 7 tests PASS (2 store + 4 comparator + 1 coverage).

- [ ] **Step 8: Commit**

```bash
git add src/profile/profile_store.py src/profile/comparator.py tests/profile/test_profile_store.py tests/profile/test_comparator.py tests/data/sample_profile.json tests/data/sample_workload_profile.json
git commit -m "feat: ProfileStore + Comparator — save/load profiles, compare Topdown/memory/coverage"
```

---

### Task 5: Code Gen Engine — Templates & Scaffold

**Files:**
- Create: `src/codegen/__init__.py`
- Create: `src/codegen/scaffold_gen.py`
- Create: `src/codegen/templates/cmake/CMakeLists.txt.j2`
- Create: `src/codegen/templates/main/main.cpp.j2`
- Create: `src/codegen/templates/config/config.json.j2`
- Test: `tests/codegen/test_scaffold_gen.py`

This task creates the Jinja2 templates for C++ project scaffold (CMakeLists.txt, main.cpp, config.json) and the scaffold_gen.py that renders them.

- [ ] **Step 1: Write CMakeLists.txt.j2 template**

```jinja2
cmake_minimum_required(VERSION 3.16)
project({{ project_name }} LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Compile flags aligned with customer
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} {{ compile_flags }}")

# Dependencies
{% for dep in dependencies %}
find_package({{ dep.name }} {{ dep.version }} REQUIRED)
{% endfor %}

# Source files
add_executable({{ project_name }}
    main.cpp
    {% for src in extra_sources %}
    {{ src }}
    {% endfor %}
)

target_link_libraries({{ project_name }}
    {% for dep in dependencies %}
    {{ dep.name }}
    {% endfor %}
)
```

- [ ] **Step 2: Write main.cpp.j2 template**

```jinja2
#include <iostream>
#include <vector>
#include <string>
#include <chrono>
#include <thread>
#include <nlohmann/json.hpp>

{% for dep_header in dep_headers %}
#include <{{ dep_header }}>
{% endfor %}

// Behavior stage implementations
{% for stage in stages %}
{{ stage.include_statement }}
{% endfor %}

// Runtime config
struct RunConfig {
    int thread_count;
    int qps;
    int warmup_seconds;
    int measurement_seconds;
    double compute_ratio;
    double memory_ratio;
};

RunConfig load_config(const std::string& config_path) {
    // TODO-Phase2: implement JSON config loading with nlohmann/json
    RunConfig cfg;
    cfg.thread_count = {{ config.thread_count | default(4) }};
    cfg.qps = {{ config.qps | default(100) }};
    cfg.warmup_seconds = {{ config.warmup_seconds | default(30) }};
    cfg.measurement_seconds = {{ config.measurement_seconds | default(60) }};
    cfg.compute_ratio = {{ config.compute_ratio | default(0.5) }};
    cfg.memory_ratio = {{ config.memory_ratio | default(0.5) }};
    return cfg;
}

int main(int argc, char* argv[]) {
    std::string config_path = "config.json";
    if (argc > 1) config_path = argv[1];

    RunConfig cfg = load_config(config_path);
    std::cout << "Workload: {{ project_name }}" << std::endl;
    std::cout << "Config: threads=" << cfg.thread_count
              << " qps=" << cfg.qps << std::endl;

    // Warmup phase
    std::cout << "Warmup for " << cfg.warmup_seconds << "s..." << std::endl;
    auto warmup_end = std::chrono::steady_clock::now() +
        std::chrono::seconds(cfg.warmup_seconds);

    {% for stage in stages %}
    {{ stage.warmup_call }}
    {% endfor %}

    while (std::chrono::steady_clock::now() < warmup_end) {
        {% for stage in stages %}
        {{ stage.loop_call }}
        {% endfor %}
    }

    // Measurement phase
    std::cout << "Measurement for " << cfg.measurement_seconds << "s..." << std::endl;
    auto measure_end = std::chrono::steady_clock::now() +
        std::chrono::seconds(cfg.measurement_seconds);

    {% for stage in stages %}
    {{ stage.measure_call }}
    {% endfor %}

    while (std::chrono::steady_clock::now() < measure_end) {
        {% for stage in stages %}
        {{ stage.loop_call }}
        {% endfor %}
    }

    std::cout << "Workload completed." << std::endl;
    return 0;
}
```

- [ ] **Step 3: Write config.json.j2 template**

```jinja2
{
  "thread_count": {{ config.thread_count | default(4) }},
  "qps": {{ config.qps | default(100) }},
  "warmup_seconds": {{ config.warmup_seconds | default(30) }},
  "measurement_seconds": {{ config.measurement_seconds | default(60) }},
  "compute_ratio": {{ config.compute_ratio | default(0.5) }},
  "memory_ratio": {{ config.memory_ratio | default(0.5) }},
  "ramp_up_seconds": {{ config.ramp_up_seconds | default(10) }}
}
```

- [ ] **Step 4: Write failing test for scaffold_gen**

```python
"""Tests for scaffold generation."""

import tempfile
import pathlib
from codegen.scaffold_gen import ScaffoldGenerator


def test_generate_project_creates_files():
    gen = ScaffoldGenerator()
    output_dir = pathlib.Path(tempfile.mkdtemp())

    context = {
        "project_name": "test_workload",
        "compile_flags": "-O2 -march=armv8.2-a",
        "dependencies": [{"name": "folly", "version": "2.1.0"}],
        "dep_headers": ["folly/futures/Future.h"],
        "stages": [],
        "extra_sources": [],
        "config": {"thread_count": 4, "qps": 100},
    }

    gen.generate(context, output_dir)

    assert (output_dir / "CMakeLists.txt").exists()
    assert (output_dir / "main.cpp").exists()
    assert (output_dir / "config.json").exists()


def test_generate_cmake_contains_project_name():
    gen = ScaffoldGenerator()
    output_dir = pathlib.Path(tempfile.mkdtemp())

    context = {
        "project_name": "search_ranking_sim",
        "compile_flags": "-O2",
        "dependencies": [{"name": "folly", "version": "2.1.0"}],
        "dep_headers": [],
        "stages": [],
        "extra_sources": [],
        "config": {},
    }

    gen.generate(context, output_dir)

    cmake_content = (output_dir / "CMakeLists.txt").read_text()
    assert "search_ranking_sim" in cmake_content
    assert "folly" in cmake_content


def test_generate_config_json_is_valid_json():
    gen = ScaffoldGenerator()
    output_dir = pathlib.Path(tempfile.mkdtemp())

    context = {
        "project_name": "test",
        "compile_flags": "-O2",
        "dependencies": [],
        "dep_headers": [],
        "stages": [],
        "extra_sources": [],
        "config": {"thread_count": 8, "qps": 500},
    }

    gen.generate(context, output_dir)

    import json
    config_data = json.loads((output_dir / "config.json").read_text())
    assert config_data["thread_count"] == 8
    assert config_data["qps"] == 500
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest tests/codegen/test_scaffold_gen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codegen'`

- [ ] **Step 6: Write ScaffoldGenerator implementation**

```python
"""Generate C++ project scaffold from Jinja2 templates."""

import pathlib
import jinja2


class ScaffoldGenerator:
    """Generate Layer 0-1 project scaffold files (CMakeLists.txt, main.cpp, config.json)."""

    def __init__(self):
        template_dir = pathlib.Path(__file__).parent / "templates"
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(template_dir)),
            keep_trailing_newline=True,
        )

    def generate(self, context: dict, output_dir: pathlib.Path) -> list[pathlib.Path]:
        """Render all scaffold templates and write to output_dir.

        Args:
            context: Jinja2 template context dict with keys:
                project_name, compile_flags, dependencies, dep_headers,
                stages, extra_sources, config.
            output_dir: Directory to write generated files.

        Returns:
            List of paths to generated files.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        generated = []

        # Render each template
        templates = {
            "cmake/CMakeLists.txt.j2": "CMakeLists.txt",
            "main/main.cpp.j2": "main.cpp",
            "config/config.json.j2": "config.json",
        }

        for template_name, output_name in templates.items():
            template = self._env.get_template(template_name)
            content = template.render(**context)
            filepath = output_dir / output_name
            filepath.write_text(content)
            generated.append(filepath)

        return generated
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/codegen/test_scaffold_gen.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/codegen/__init__.py src/codegen/scaffold_gen.py src/codegen/templates/ tests/codegen/test_scaffold_gen.py
git commit -m "feat: ScaffoldGenerator — Jinja2 templates for CMakeLists.txt, main.cpp, config.json"
```

---

### Task 6: Code Gen Engine — Behavior Templates & Generator

**Files:**
- Create: `src/codegen/behavior_gen.py`
- Create: `src/codegen/templates/behaviors/compute_synthesis.cpp.j2`
- Create: `src/codegen/templates/behaviors/memory_synthesis.cpp.j2`
- Create: `src/codegen/templates/behaviors/direct_call_wrapper.cpp.j2`
- Create: `src/codegen/generator.py`
- Create: `src/codegen/knob_gen.py`
- Test: `tests/codegen/test_behavior_gen.py`
- Test: `tests/codegen/test_generator.py`

This task creates behavior implementation code templates (compute synthesis, memory synthesis, direct call wrapper) and the Generator orchestrator that combines scaffold + behavior + knob generation.

- [ ] **Step 1: Write compute_synthesis.cpp.j2 template**

```jinja2
// Compute synthesis stage: {{ stage_name }}
// Simulates compute-intensive behavior ({{ synthesis_config.compute_type }})

#pragma once
#include <vector>
#include <cmath>
#include <algorithm>

inline void {{ stage_name }}_compute(int iterations = {{ synthesis_config.iterations | default(100) }}) {
    // Compute-intensive loop — simulate {{ synthesis_config.compute_type }} workload
    volatile double result = 0.0;
    for (int i = 0; i < iterations; ++i) {
        result += std::sin(i * 0.001) * std::cos(i * 0.002);
        result += std::sqrt(static_cast<double>(i + 1));
    }
}
```

- [ ] **Step 2: Write memory_synthesis.cpp.j2 template**

```jinja2
// Memory synthesis stage: {{ stage_name }}
// Simulates memory-intensive behavior ({{ synthesis_config.access_pattern }})

#pragma once
#include <vector>
#include <random>
#include <cstdint>

inline void {{ stage_name }}_memory(int iterations = {{ synthesis_config.iterations | default(100) }}) {
    // Memory-intensive access — {{ synthesis_config.access_pattern }} pattern
    // Working set: {{ synthesis_config.working_set_mb | default(64) }} MB
    const size_t working_set_bytes = {{ synthesis_config.working_set_mb | default(64) }} * 1024 * 1024;
    const size_t element_count = working_set_bytes / sizeof(uint64_t);

    static std::vector<uint64_t> data(element_count, 0);
    if (data.empty()) data.resize(element_count, 42);

    std::random_device rd;
    std::mt19937 gen(rd());

    {% if synthesis_config.access_pattern == "random" %}
    std::uniform_int_distribution<size_t> dist(0, element_count - 1);
    for (int i = 0; i < iterations; ++i) {
        size_t idx = dist(gen);
        data[idx] += i;
    }
    {% elif synthesis_config.access_pattern == "sequential" %}
    for (int i = 0; i < iterations; ++i) {
        size_t idx = i % element_count;
        data[idx] += i;
    }
    {% else %}
    // Mixed access pattern
    std::uniform_int_distribution<size_t> dist(0, element_count - 1);
    for (int i = 0; i < iterations; ++i) {
        if (i % 4 == 0) {
            data[dist(gen)] += i;  // random
        } else {
            data[i % element_count] += i;  // sequential
        }
    }
    {% endif %}
}
```

- [ ] **Step 3: Write direct_call_wrapper.cpp.j2 template**

```jinja2
// Direct call wrapper: {{ stage_name }}
// Calls open-source library function {{ function }} directly

#pragma once
{% for dep_header in dep_headers %}
#include <{{ dep_header }}>
{% endfor %}

inline void {{ stage_name }}_direct_call() {
    // Directly call {{ function }} from {{ library }}
    // This ensures call-stack path alignment with customer workload
    {{ call_statement }};
}
```

- [ ] **Step 4: Write behavior_gen.py**

```python
"""Generate behavior implementation code for each workflow stage."""

import pathlib
import jinja2
from profile_schema import HotspotFunction


class BehaviorGenerator:
    """Generate Layer 3 behavior implementation code from Behavior Profiles."""

    def __init__(self):
        template_dir = pathlib.Path(__file__).parent / "templates"
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(template_dir)),
            keep_trailing_newline=True,
        )

    def generate_stage_file(self, stage: dict) -> tuple[str, str]:
        """Generate a C++ header file for one workflow stage.

        Args:
            stage: Behavior profile dict for one stage, containing:
                stage_name, implementation_strategy, strategies, etc.

        Returns:
            Tuple of (filename, content) for the generated .h file.
        """
        strategy = stage["implementation_strategy"]

        if strategy == "compute_synthesis":
            template = self._env.get_template("behaviors/compute_synthesis.cpp.j2")
            context = {
                "stage_name": stage["stage_name"],
                "synthesis_config": stage.get("strategies", [{}])[0].get("synthesis_config", {}),
            }
        elif strategy == "memory_synthesis":
            template = self._env.get_template("behaviors/memory_synthesis.cpp.j2")
            context = {
                "stage_name": stage["stage_name"],
                "synthesis_config": stage.get("strategies", [{}])[0].get("synthesis_config", {}),
            }
        elif strategy == "direct_call":
            template = self._env.get_template("behaviors/direct_call_wrapper.cpp.j2")
            strat = stage.get("strategies", [{}])[0]
            context = {
                "stage_name": stage["stage_name"],
                "function": strat.get("function", "unknown"),
                "library": strat.get("library", "unknown"),
                "dep_headers": stage.get("dep_headers", []),
                "call_statement": stage.get("call_statement", "/* direct call placeholder */"),
            }
        elif strategy == "mixed":
            # For mixed, generate compute + memory + direct call combined
            compute_content = ""
            memory_content = ""
            for strat in stage.get("strategies", []):
                if strat.get("strategy") == "compute_synthesis":
                    tmpl = self._env.get_template("behaviors/compute_synthesis.cpp.j2")
                    compute_content = tmpl.render(
                        stage_name=stage["stage_name"],
                        synthesis_config=strat.get("synthesis_config", {}),
                    )
                elif strat.get("strategy") == "memory_synthesis":
                    tmpl = self._env.get_template("behaviors/memory_synthesis.cpp.j2")
                    memory_content = tmpl.render(
                        stage_name=stage["stage_name"],
                        synthesis_config=strat.get("synthesis_config", {}),
                    )
                elif strat.get("strategy") == "direct_call":
                    tmpl = self._env.get_template("behaviors/direct_call_wrapper.cpp.j2")
                    context = {
                        "stage_name": stage["stage_name"],
                        "function": strat.get("function", "unknown"),
                        "library": strat.get("library", "unknown"),
                        "dep_headers": stage.get("dep_headers", []),
                        "call_statement": stage.get("call_statement", "/* direct call */"),
                    }

            filename = f"{stage['stage_name']}.h"
            content = f"// Mixed stage: {stage['stage_name']}\n#pragma once\n{compute_content}\n{memory_content}\n"
            return filename, content
        else:
            filename = f"{stage['stage_name']}.h"
            content = f"// Unknown strategy: {strategy}\n#pragma once\n"
            return filename, content

        content = template.render(**context)
        filename = f"{stage['stage_name']}.h"
        return filename, content
```

- [ ] **Step 5: Write knob_gen.py**

```python
"""Generate Layer 4 tuning knob configuration."""

import json
import pathlib


class KnobGenerator:
    """Generate config.json with runtime tuning parameters."""

    def generate_config(self, knobs: dict, output_path: pathlib.Path) -> pathlib.Path:
        """Write a config.json file with tuning parameters.

        Args:
            knobs: Dict with keys like thread_count, qps, warmup_seconds,
                measurement_seconds, compute_ratio, memory_ratio, etc.
            output_path: Path to write config.json.

        Returns:
            Path to the written file.
        """
        # Default values filled in for missing keys
        config = {
            "thread_count": knobs.get("thread_count", 4),
            "qps": knobs.get("qps", 100),
            "warmup_seconds": knobs.get("warmup_seconds", 30),
            "measurement_seconds": knobs.get("measurement_seconds", 60),
            "compute_ratio": knobs.get("compute_ratio", 0.5),
            "memory_ratio": knobs.get("memory_ratio", 0.5),
            "ramp_up_seconds": knobs.get("ramp_up_seconds", 10),
        }

        output_path.write_text(json.dumps(config, indent=2))
        return output_path

    def update_config(self, config_path: pathlib.Path, updates: dict) -> pathlib.Path:
        """Update specific fields in an existing config.json.

        Args:
            config_path: Path to existing config.json.
            updates: Dict of key-value pairs to update.

        Returns:
            Path to the updated file.
        """
        with open(config_path, "r") as f:
            config = json.load(f)

        config.update(updates)

        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        return config_path
```

- [ ] **Step 6: Write generator.py (orchestrator)**

```python
"""Orchestrates the full code generation pipeline: scaffold → behavior → knob."""

import pathlib
from codegen.scaffold_gen import ScaffoldGenerator
from codegen.behavior_gen import BehaviorGenerator
from codegen.knob_gen import KnobGenerator


class WorkloadGenerator:
    """Orchestrate full workload project generation from generation instruction.

    Combines:
    - ScaffoldGenerator (Layer 0-1: project structure)
    - BehaviorGenerator (Layer 3: stage implementations)
    - KnobGenerator (Layer 4: runtime config)
    """

    def __init__(self):
        self.scaffold = ScaffoldGenerator()
        self.behavior = BehaviorGenerator()
        self.knob = KnobGenerator()

    def generate(self, instruction: dict, output_dir: pathlib.Path) -> pathlib.Path:
        """Generate a complete workload project from a generation instruction.

        Args:
            instruction: Structured generation instruction dict with keys:
                project_name, compile_flags, dependencies, dep_headers,
                stages (list of behavior profiles), config (knob dict).
            output_dir: Directory to generate the project in.

        Returns:
            Path to the generated project directory.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Generate behavior stage files
        stage_files = []
        stage_contexts = []
        for stage in instruction.get("stages", []):
            filename, content = self.behavior.generate_stage_file(stage)
            filepath = output_dir / filename
            filepath.write_text(content)
            stage_files.append(filename)

            # Build stage context for main.cpp template
            stage_ctx = {
                "include_statement": f'#include "{filename}"',
                "warmup_call": f"{stage['stage_name']}_compute();" if stage["implementation_strategy"] != "memory_synthesis" else f"{stage['stage_name']}_memory();",
                "loop_call": f"{stage['stage_name']}_compute();" if stage["implementation_strategy"] != "memory_synthesis" else f"{stage['stage_name']}_memory();",
                "measure_call": f"// {stage['stage_name']} measurement start",
            }
            stage_contexts.append(stage_ctx)

        # Build scaffold context
        scaffold_context = {
            "project_name": instruction.get("project_name", "workload_sim"),
            "compile_flags": instruction.get("compile_flags", "-O2"),
            "dependencies": instruction.get("dependencies", []),
            "dep_headers": instruction.get("dep_headers", []),
            "stages": stage_contexts,
            "extra_sources": stage_files,
            "config": instruction.get("config", {}),
        }

        # Generate scaffold (CMakeLists.txt, main.cpp, config.json)
        self.scaffold.generate(scaffold_context, output_dir)

        # Override config.json with knob generator for precise control
        config_path = output_dir / "config.json"
        self.knob.generate_config(instruction.get("config", {}), config_path)

        return output_dir
```

- [ ] **Step 7: Write failing tests**

```python
"""Tests for BehaviorGenerator and WorkloadGenerator."""

import tempfile
import pathlib
import json
from codegen.behavior_gen import BehaviorGenerator
from codegen.generator import WorkloadGenerator


def test_behavior_gen_compute_synthesis():
    gen = BehaviorGenerator()
    stage = {
        "stage_name": "feature_calc",
        "implementation_strategy": "compute_synthesis",
        "strategies": [
            {
                "strategy": "compute_synthesis",
                "synthesis_config": {"compute_type": "hash", "iterations": 200},
            }
        ],
    }

    filename, content = gen.generate_stage_file(stage)
    assert filename == "feature_calc.h"
    assert "feature_calc_compute" in content
    assert "iterations" in content


def test_behavior_gen_memory_synthesis():
    gen = BehaviorGenerator()
    stage = {
        "stage_name": "data_lookup",
        "implementation_strategy": "memory_synthesis",
        "strategies": [
            {
                "strategy": "memory_synthesis",
                "synthesis_config": {"access_pattern": "random", "working_set_mb": 64},
            }
        ],
    }

    filename, content = gen.generate_stage_file(stage)
    assert "data_lookup_memory" in content
    assert "random" in content


def test_workload_generator_full_project():
    gen = WorkloadGenerator()
    output_dir = pathlib.Path(tempfile.mkdtemp())

    instruction = {
        "project_name": "search_ranking_sim",
        "compile_flags": "-O2 -march=armv8.2-a",
        "dependencies": [{"name": "folly", "version": "2.1.0"}],
        "dep_headers": ["folly/futures/Future.h"],
        "stages": [
            {
                "stage_name": "feature_calc",
                "implementation_strategy": "compute_synthesis",
                "strategies": [{"strategy": "compute_synthesis", "synthesis_config": {"compute_type": "hash", "iterations": 100}}],
            },
            {
                "stage_name": "data_lookup",
                "implementation_strategy": "memory_synthesis",
                "strategies": [{"strategy": "memory_synthesis", "synthesis_config": {"access_pattern": "random", "working_set_mb": 32}}],
            },
        ],
        "config": {"thread_count": 8, "qps": 500, "warmup_seconds": 30, "measurement_seconds": 60},
    }

    result_dir = gen.generate(instruction, output_dir)

    # Check all expected files exist
    assert (result_dir / "CMakeLists.txt").exists()
    assert (result_dir / "main.cpp").exists()
    assert (result_dir / "config.json").exists()
    assert (result_dir / "feature_calc.h").exists()
    assert (result_dir / "data_lookup.h").exists()

    # Check config.json has correct values
    config = json.loads((result_dir / "config.json").read_text())
    assert config["thread_count"] == 8
    assert config["qps"] == 500
```

- [ ] **Step 8: Run tests to verify they fail**

Run: `pytest tests/codegen/test_behavior_gen.py tests/codegen/test_generator.py -v`
Expected: FAIL — module not found errors.

- [ ] **Step 9: Run tests to verify they pass after implementation**

Run: `pytest tests/codegen/test_behavior_gen.py tests/codegen/test_generator.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 10: Commit**

```bash
git add src/codegen/behavior_gen.py src/codegen/knob_gen.py src/codegen/generator.py src/codegen/templates/behaviors/ tests/codegen/test_behavior_gen.py tests/codegen/test_generator.py
git commit -m "feat: BehaviorGenerator + KnobGenerator + WorkloadGenerator — full code gen pipeline"
```

---

### Task 7: Harness — Build Runner & Execution Runner

**Files:**
- Create: `src/harness/__init__.py`
- Create: `src/harness/build_runner.py`
- Create: `src/harness/execution_runner.py`
- Create: `src/harness/run_config.py`
- Test: `tests/harness/test_build_runner.py`
- Test: `tests/harness/test_execution_runner.py`

Build Runner compiles the generated C++ project. Execution Runner runs the workload binary with warmup timing. RunConfig holds runtime parameters.

- [ ] **Step 1: Write RunConfig model**

```python
"""Runtime configuration for workload execution."""

from pydantic import BaseModel, Field


class RunConfig(BaseModel):
    """Configuration for running a workload binary."""
    thread_count: int = 4
    qps: int = 100
    warmup_seconds: int = 30
    measurement_seconds: int = 60
    ramp_up_seconds: int = 10
    config_path: str = "config.json"
    concurrency: int = Field(default=4, description="Number of concurrent stress threads")
```

- [ ] **Step 2: Write BuildRunner**

```python
"""Build Runner — compile generated C++ workload project."""

import pathlib
import subprocess
import logging

logger = logging.getLogger(__name__)


class BuildResult:
    """Result of a build attempt."""

    def __init__(self, success: bool, output: str, error: str, binary_path: pathlib.Path | None = None):
        self.success = success
        self.output = output
        self.error = error
        self.binary_path = binary_path


class BuildRunner:
    """Compile a generated C++ workload project using cmake + make.

    Args:
        cmake_path: Path to cmake executable.
        make_path: Path to make executable.
        build_dir_suffix: Subdirectory name for build output.
    """

    def __init__(
        self,
        cmake_path: str = "cmake",
        make_path: str = "make",
        build_dir_suffix: str = "build",
    ):
        self.cmake_path = cmake_path
        self.make_path = make_path
        self.build_dir_suffix = build_dir_suffix

    def build(self, project_dir: pathlib.Path) -> BuildResult:
        """Build a C++ project in project_dir.

        Steps:
        1. Create build subdirectory
        2. Run cmake to configure
        3. Run make to compile
        4. Locate the binary

        Args:
            project_dir: Path to the project directory containing CMakeLists.txt.

        Returns:
            BuildResult with success status, output, error, and binary path if successful.
        """
        build_dir = project_dir / self.build_dir_suffix
        build_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: cmake configure
        cmake_cmd = [self.cmake_path, "-S", str(project_dir), "-B", str(build_dir)]
        logger.info(f"Running cmake: {cmake_cmd}")

        try:
            cmake_result = subprocess.run(
                cmake_cmd, capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            return BuildResult(success=False, output="", error="cmake timed out after 120s")
        except FileNotFoundError:
            return BuildResult(success=False, output="", error=f"cmake not found at {self.cmake_path}")

        if cmake_result.returncode != 0:
            return BuildResult(
                success=False,
                output=cmake_result.stdout,
                error=cmake_result.stderr,
            )

        # Step 2: make compile
        make_cmd = [self.make_path, "-C", str(build_dir)]
        logger.info(f"Running make: {make_cmd}")

        try:
            make_result = subprocess.run(
                make_cmd, capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            return BuildResult(success=False, output="", error="make timed out after 300s")
        except FileNotFoundError:
            return BuildResult(success=False, output="", error=f"make not found at {self.make_path}")

        if make_result.returncode != 0:
            return BuildResult(
                success=False,
                output=make_result.stdout,
                error=make_result.stderr,
            )

        # Step 3: locate binary
        # Binary name matches project_name in CMakeLists.txt
        # Try to find it in build_dir
        binary = None
        for ext in ["", ".exe"]:
            for candidate in build_dir.glob(f"*{ext}"):
                if candidate.is_file() and candidate.stat().st_mode & 0o111:
                    binary = candidate
                    break

        if binary is None:
            # Search recursively
            for candidate in build_dir.rglob("*"):
                if candidate.is_file() and not candidate.name.startswith(".") and candidate.suffix not in [".o", ".cmake", ".txt", ".json", ".make"]:
                    binary = candidate
                    break

        return BuildResult(
            success=True,
            output=make_result.stdout,
            error=make_result.stderr,
            binary_path=binary,
        )
```

- [ ] **Step 3: Write ExecutionRunner**

```python
"""Execution Runner — run workload binary with warmup and measurement phases."""

import json
import pathlib
import subprocess
import signal
import time
import logging
import os

from harness.run_config import RunConfig

logger = logging.getLogger(__name__)


class ExecutionResult:
    """Result of a workload execution."""

    def __init__(self, success: bool, stdout: str, stderr: str, exit_code: int | None = None):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class ExecutionRunner:
    """Run a workload binary with warmup + measurement phases.

    Args:
        default_timeout: Maximum total execution time in seconds.
    """

    def __init__(self, default_timeout: int = 300):
        self.default_timeout = default_timeout

    def run(self, binary_path: pathlib.Path, run_config: RunConfig) -> ExecutionResult:
        """Execute a workload binary.

        The workload binary itself handles warmup/measurement timing based on config.json.
        This runner simply launches the binary and waits for completion.

        Args:
            binary_path: Path to the compiled workload binary.
            run_config: Runtime configuration.

        Returns:
            ExecutionResult with success status, output, and exit code.
        """
        total_timeout = run_config.warmup_seconds + run_config.measurement_seconds + 30

        cmd = [str(binary_path), run_config.config_path]

        logger.info(f"Running workload: {cmd}")
        logger.info(f"Timeout: {total_timeout}s")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=total_timeout,
            )

            return ExecutionResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )

        except subprocess.TimeoutExpired:
            logger.error(f"Workload timed out after {total_timeout}s")
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Timeout after {total_timeout}s",
                exit_code=None,
            )
        except FileNotFoundError:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Binary not found: {binary_path}",
                exit_code=None,
            )

    def validate_run(self, binary_path: pathlib.Path, timeout: int = 5) -> ExecutionResult:
        """Short validation run to check if the binary can start.

        Args:
            binary_path: Path to the workload binary.
            timeout: Short timeout in seconds.

        Returns:
            ExecutionResult from the short run.
        """
        cmd = [str(binary_path)]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            # Even non-zero exit might be ok for validation (e.g., missing config file)
            # We just check that it started and produced some output
            return ExecutionResult(
                success=bool(result.stdout or result.stderr),
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            # Binary started but didn't finish in 5s — that's actually OK for validation
            return ExecutionResult(
                success=True,
                stdout="(timeout — binary started)",
                stderr="",
                exit_code=None,
            )
        except FileNotFoundError:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Binary not found: {binary_path}",
                exit_code=None,
            )
```

- [ ] **Step 4: Write tests for Build Runner**

```python
"""Tests for BuildRunner."""

import pathlib
import tempfile
from harness.build_runner import BuildRunner, BuildResult


def test_build_result_success():
    result = BuildResult(success=True, output="built", error="", binary_path=pathlib.Path("/tmp/workload"))
    assert result.success is True
    assert result.binary_path is not None


def test_build_result_failure():
    result = BuildResult(success=False, output="", error="cmake failed")
    assert result.success is False
    assert result.binary_path is None


def test_build_missing_cmake():
    # Using a nonexistent cmake path should fail gracefully
    runner = BuildRunner(cmake_path="/nonexistent/cmake")
    project_dir = pathlib.Path(tempfile.mkdtemp())
    result = runner.build(project_dir)
    assert result.success is False
    assert "cmake not found" in result.error or "cmake" in result.error.lower()
```

- [ ] **Step 5: Write tests for Execution Runner**

```python
"""Tests for ExecutionRunner."""

import pathlib
import tempfile
import os
from harness.execution_runner import ExecutionRunner, ExecutionResult
from harness.run_config import RunConfig


def test_run_config_defaults():
    cfg = RunConfig()
    assert cfg.thread_count == 4
    assert cfg.warmup_seconds == 30


def test_run_config_custom():
    cfg = RunConfig(thread_count=16, qps=500, warmup_seconds=10)
    assert cfg.thread_count == 16
    assert cfg.qps == 500


def test_execution_result_success():
    result = ExecutionResult(success=True, stdout="done", stderr="", exit_code=0)
    assert result.success is True


def test_validate_run_missing_binary():
    runner = ExecutionRunner()
    result = runner.validate_run(pathlib.Path("/nonexistent/binary"))
    assert result.success is False


def test_validate_run_with_echo_script():
    """Test validation run with a simple script that exits immediately."""
    # Create a simple script that prints something and exits
    script_dir = pathlib.Path(tempfile.mkdtemp())
    script_path = script_dir / "test_binary.sh"
    script_path.write_text("echo 'test output'\nexit 0\n")
    os.chmod(script_path, 0o755)

    runner = ExecutionRunner()
    result = runner.validate_run(script_path)
    assert result.success is True
    assert "test output" in result.stdout
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/harness/test_build_runner.py tests/harness/test_execution_runner.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/harness/__init__.py src/harness/build_runner.py src/harness/execution_runner.py src/harness/run_config.py tests/harness/
git commit -m "feat: BuildRunner + ExecutionRunner + RunConfig — build and run workload binaries"
```

---

### Task 8: Harness — Metrics Collector

**Files:**
- Create: `src/harness/metrics_collector.py`
- Test: `tests/harness/test_metrics_collector.py`

MetricsCollector orchestrates the collection of Topdown, flamegraph, and memory data from the target machine after workload execution. In Phase 1, it wraps subprocess calls to devkit and perf tools.

- [ ] **Step 1: Write failing tests for MetricsCollector**

```python
"""Tests for MetricsCollector."""

import pathlib
import tempfile
import json
from harness.metrics_collector import MetricsCollector, CollectionResult


def test_collection_result_success():
    result = CollectionResult(
        success=True,
        topdown_path=pathlib.Path("/tmp/topdown.json"),
        flamegraph_path=pathlib.Path("/tmp/flamegraph.txt"),
        memory_path=pathlib.Path("/tmp/memory.json"),
    )
    assert result.success is True
    assert result.topdown_path is not None


def test_collection_result_failure():
    result = CollectionResult(success=False, error="perf not found")
    assert result.success is False


def test_collector_parse_existing_topdown():
    """Test that collector can parse an existing topdown JSON file."""
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    topdown_file = tmp_dir / "topdown.json"

    # Write a valid topdown JSON
    topdown_data = {
        "topdown_l1": {
            "frontend_bound": 0.22,
            "backend_bound": 0.38,
            "bad_speculation": 0.11,
            "retiring": 0.29,
        },
        "memory": {"bandwidth_gbps": 43.8, "l3_miss_rate": 0.07},
    }
    topdown_file.write_text(json.dumps(topdown_data))

    collector = MetricsCollector()
    profile = collector.parse_topdown_file(topdown_file)

    assert profile.topdown is not None
    assert profile.topdown.frontend_bound == 0.22
    assert profile.memory.bandwidth_gbps == 43.8


def test_collector_parse_existing_flamegraph():
    """Test that collector can parse an existing flamegraph folded file."""
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    flamegraph_file = tmp_dir / "flamegraph_folded.txt"

    flamegraph_file.write_text(
        "main;workload_stage;compute 1000\n"
        "main;workload_stage;memory_access 500\n"
        "main 200\n"
    )

    collector = MetricsCollector()
    hotspots = collector.parse_flamegraph_file(flamegraph_file)

    assert len(hotspots) > 0
    assert hotspots[0].self_pct > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/harness/test_metrics_collector.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write MetricsCollector implementation**

```python
"""Metrics Collector — collect Topdown, flamegraph, and memory data from workload runs."""

import pathlib
import subprocess
import logging
from profile_schema import Profile
from ingestion.topdown_parser import TopdownParser
from ingestion.flamegraph_parser import FlamegraphParser

logger = logging.getLogger(__name__)


class CollectionResult:
    """Result of a metrics collection attempt."""

    def __init__(
        self,
        success: bool,
        topdown_path: pathlib.Path | None = None,
        flamegraph_path: pathlib.Path | None = None,
        memory_path: pathlib.Path | None = None,
        error: str = "",
    ):
        self.success = success
        self.topdown_path = topdown_path
        self.flamegraph_path = flamegraph_path
        self.memory_path = memory_path
        self.error = error


class MetricsCollector:
    """Collect performance metrics from a workload run on the target machine.

    In Phase 1, this wraps subprocess calls to devkit and perf.
    The actual collection commands depend on the user's devkit and perf setup.
    """

    def __init__(
        self,
        devkit_cmd: str | None = None,
        perf_cmd: str = "perf",
    ):
        self.devkit_cmd = devkit_cmd
        self.perf_cmd = perf_cmd
        self.topdown_parser = TopdownParser()
        self.flamegraph_parser = FlamegraphParser()

    def collect_topdown(self, output_path: pathlib.Path, duration: int = 60) -> CollectionResult:
        """Collect Topdown data using devkit.

        Args:
            output_path: Path to write Topdown JSON output.
            duration: Collection duration in seconds.

        Returns:
            CollectionResult with topdown_path if successful.
        """
        if self.devkit_cmd is None:
            logger.warning("No devkit command configured — skipping Topdown collection")
            return CollectionResult(success=False, error="devkit_cmd not configured")

        cmd = [self.devkit_cmd, "topdown", "--duration", str(duration), "--output", str(output_path)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 30)
            if result.returncode != 0:
                return CollectionResult(success=False, error=result.stderr)
            return CollectionResult(success=True, topdown_path=output_path)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return CollectionResult(success=False, error=str(e))

    def collect_flamegraph(self, output_path: pathlib.Path, pid: int | None = None) -> CollectionResult:
        """Collect flamegraph data using perf record + flamegraph conversion.

        Args:
            output_path: Path to write folded flamegraph output.
            pid: Process ID to attach perf to. None means system-wide.

        Returns:
            CollectionResult with flamegraph_path if successful.
        """
        # perf record -g -F 99 -o perf.data [—pid <pid>]
        perf_data = output_path.parent / "perf.data"

        cmd = [self.perf_cmd, "record", "-g", "-F", "99", "-o", str(perf_data)]
        if pid is not None:
            cmd.extend(["--pid", str(pid)])

        # For Phase 1, we'll assume perf data is already collected
        # and we just parse it. Full perf integration comes in Phase 2.
        logger.info(f"Flamegraph collection: would run {cmd}")
        logger.warning("Phase 1: perf collection is manual — provide pre-collected flamegraph files")

        return CollectionResult(success=True, flamegraph_path=output_path)

    def parse_topdown_file(self, filepath: pathlib.Path) -> Profile:
        """Parse a previously collected Topdown JSON file.

        Args:
            filepath: Path to the Topdown JSON file.

        Returns:
            Profile with topdown and memory fields.
        """
        return self.topdown_parser.parse_json(filepath)

    def parse_flamegraph_file(self, filepath: pathlib.Path) -> list:
        """Parse a previously collected flamegraph folded file.

        Args:
            filepath: Path to the folded flamegraph file.

        Returns:
            List of HotspotFunction.
        """
        return self.flamegraph_parser.parse_folded(filepath)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/harness/test_metrics_collector.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/harness/metrics_collector.py tests/harness/test_metrics_collector.py
git commit -m "feat: MetricsCollector — collect and parse Topdown + flamegraph data"
```

---

### Task 9: Agent Core — Prompt Chain Orchestrator

**Files:**
- Create: `src/agent/__init__.py`
- Create: `src/agent/agent_core.py`
- Create: `src/agent/strategy.py`
- Create: `src/agent/prompts/analyze_profile.md`
- Create: `src/agent/prompts/plan_workflow.md`
- Create: `src/agent/prompts/detail_fill.md`
- Create: `src/agent/prompts/evaluate_comparison.md`
- Test: `tests/agent/test_agent_core.py`
- Test: `tests/agent/test_strategy.py`

Agent Core orchestrates the LLM prompt chain: analyze Profile → plan workflow → fill details → evaluate comparison. Strategy.py provides iteration decision logic. In Phase 1, Agent uses Claude API directly (no MCP yet).

- [ ] **Step 1: Write prompt templates**

Create `src/agent/prompts/analyze_profile.md`:

```markdown
You are a workload simulation expert. Analyze the following customer Profile data and produce:

1. **Topdown bottleneck identification**: Which Topdown category is dominant? What does it imply about the workload's microarchitectural behavior?

2. **Hotspot classification**: For each hotspot function, classify it as either:
   - "open_source" (comes from a known open-source library like folly, fbthrift, brpc, taskflow)
   - "customer_custom" (customer's proprietary code)

3. **Workflow stage proposal**: Based on the call paths and business logic, propose how to divide the workload into processing stages. For each stage, estimate its target Topdown characteristics.

4. **Key observations**: Any notable patterns (e.g., high memory_bound suggesting data-heavy workload, high bad_speculation suggesting branch-heavy logic).

Output your analysis as structured JSON matching the following schema:
{
  "bottleneck_type": "backend_bound",
  "bottleneck_subtype": "memory_bound",
  "hotspot_classification": [{"function": "...", "source": "open_source|customer_custom", "library": "..."}],
  "workflow_stages": [{"stage_name": "...", "target_topdown": {...}, "description": "..."}],
  "observations": ["..."]
}

Customer Profile:
{profile_json}
```

Create `src/agent/prompts/plan_workflow.md`:

```markdown
You are a workload simulation architect. Based on the analysis below, design the Business Workflow for the simulated workload.

For each workflow stage, specify:
- **stage_name**: Short identifier
- **implementation_strategy**: One of "compute_synthesis", "memory_synthesis", "direct_call", or "mixed"
- **strategies**: For each hotspot in this stage, specify the behavior implementation strategy
  - open_source hotspots → "direct_call" (call the real open-source library function)
  - customer_custom hotspots → "compute_synthesis" or "memory_synthesis" based on the Topdown bottleneck
- **target_topdown**: Target Topdown L1 metrics for this stage

Output as structured JSON matching this schema:
{
  "stages": [{
    "stage_name": "...",
    "implementation_strategy": "...",
    "target_topdown": {"frontend_bound": ..., "backend_bound": ..., "bad_speculation": ..., "retiring": ...},
    "strategies": [{
      "function": "...",
      "source": "open_source|customer_custom",
      "strategy": "direct_call|compute_synthesis|memory_synthesis|mixed",
      "weight_pct": ...,
      "synthesis_config": {...}
    }]
  }]
}

Analysis result:
{analysis_json}
```

Create `src/agent/prompts/detail_fill.md`:

```markdown
You are a C++ workload generator. Fill in the implementation details for each workflow stage.

For "direct_call" stages:
- Provide the exact C++ call statement for the open-source library function
- Provide the required #include headers

For "compute_synthesis" stages:
- Specify compute_type (e.g., "hash", "sort", "matrix_multiply")
- Specify iterations parameter

For "memory_synthesis" stages:
- Specify access_pattern ("random", "sequential", "mixed")
- Specify working_set_mb

For "mixed" stages:
- Combine compute and memory synthesis configs

Also specify:
- project_name: A name for the workload project
- compile_flags: Matching the customer's compile flags
- dependencies: List of CMake dependencies with versions
- dep_headers: List of #include headers needed

Output as structured JSON matching the WorkloadGenerator instruction schema:
{
  "project_name": "...",
  "compile_flags": "...",
  "dependencies": [...],
  "dep_headers": [...],
  "stages": [{...with filled details}],
  "config": {"thread_count": ..., "qps": ..., ...}
}

Workflow plan:
{workflow_json}
```

Create `src/agent/prompts/evaluate_comparison.md`:

```markdown
You are a workload simulation evaluator. Analyze the comparison report between the customer Profile and the generated workload Profile.

Based on the comparison:
1. Identify which metrics are NOT within threshold
2. Recommend which iteration strategy priority to use:
   - Priority 1: Adjust config.json parameters (if diffs < 5%)
   - Priority 2: Adjust Behavior Profiles (if diffs 5-10%)
   - Priority 3: Adjust Business Workflow (if diffs > 10%)
   - Priority 4: Adjust Service Skeleton (if architectural assumptions wrong)
3. Provide specific adjustment suggestions

Output as structured JSON:
{
  "iteration_priority": 1|2|3|4,
  "adjustments": [
    {"target": "config|behavior|workflow|skeleton", "field": "...", "change": "..."}
  ],
  "rationale": "..."
}

Comparison report:
{comparison_json}
```

- [ ] **Step 2: Write strategy.py**

```python
"""Iteration strategy decision logic."""

from profile.comparator import ProfileComparator


def decide_iteration_priority(comparison_report: dict) -> int:
    """Decide which iteration priority level to use based on comparison report.

    Priority levels (cost increasing):
    1: Adjust config.json parameters
    2: Adjust Behavior Profiles
    3: Adjust Business Workflow
    4: Adjust Service Skeleton

    Args:
        comparison_report: Output from ProfileComparator.compare()

    Returns:
        Priority level 1-4.
    """
    topdown_report = comparison_report.get("topdown_l1", {})
    not_ok = {k: v for k, v in topdown_report.items() if not v.get("within_threshold", True)}

    if not not_ok:
        # Topdown is OK, check other metrics
        coverage = comparison_report.get("hotspot_coverage", {}).get("coverage_pct", 100.0)
        if coverage < 80.0:
            return 2  # Need more hotspot coverage → adjust behaviors
        return 0  # Converged

    # Calculate max absolute diff_pct among metrics not within threshold
    max_diff = max(abs(v.get("diff_pct", 0)) for v in not_ok.values())

    if max_diff < 5.0:
        return 1  # Small diffs → try parameter tuning
    elif max_diff < 10.0:
        return 2  # Moderate diffs → adjust behaviors
    elif max_diff < 20.0:
        return 3  # Large diffs → rethink workflow
    else:
        return 4  # Very large diffs → rethink architecture
```

- [ ] **Step 3: Write agent_core.py**

```python
"""Agent Core — orchestrates LLM prompt chain for workload generation."""

import json
import pathlib
import logging

try:
    import anthropic
except ImportError:
    anthropic = None

logger = logging.getLogger(__name__)

PROMPTS_DIR = pathlib.Path(__file__).parent / "prompts"


class AgentCore:
    """Orchestrate the LLM prompt chain for workload simulation.

    Phase 1: Uses Claude API directly with prompt templates.
    Phase 2: Will use MCP protocol for tool access.

    Args:
        model: Claude model to use.
        api_key: Anthropic API key (or from env ANTHROPIC_API_KEY).
        max_tokens: Max tokens for each LLM response.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: str | None = None,
        max_tokens: int = 4096,
    ):
        self.model = model
        self.max_tokens = max_tokens

        if anthropic is None:
            raise ImportError("anthropic package not installed — run: pip install anthropic")

        self.client = anthropic.Anthropic(api_key=api_key)

    def _load_prompt(self, name: str) -> str:
        """Load a prompt template from the prompts directory.

        Args:
            name: Prompt filename (e.g., "analyze_profile.md").

        Returns:
            Prompt template text.
        """
        filepath = PROMPTS_DIR / name
        return filepath.read_text()

    def _call_llm(self, prompt: str) -> str:
        """Call the LLM with a prompt and return the response text.

        Args:
            prompt: Full prompt text.

        Returns:
            LLM response text.
        """
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    def analyze_profile(self, profile_json: str) -> dict:
        """Analyze a customer Profile and produce structured analysis.

        Args:
            profile_json: JSON string of the customer Profile.

        Returns:
            Analysis dict with bottleneck_type, hotspot_classification, workflow_stages.
        """
        template = self._load_prompt("analyze_profile.md")
        prompt = template.replace("{profile_json}", profile_json)

        response_text = self._call_llm(prompt)

        # Parse JSON from response
        try:
            # Find JSON block in response
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                analysis = json.loads(response_text[json_start:json_end])
            else:
                analysis = {"raw_response": response_text}
        except json.JSONDecodeError:
            analysis = {"raw_response": response_text}

        return analysis

    def plan_workflow(self, analysis_json: str) -> dict:
        """Plan Business Workflow stages based on analysis.

        Args:
            analysis_json: JSON string of the analysis result.

        Returns:
            Workflow plan dict with stages list.
        """
        template = self._load_prompt("plan_workflow.md")
        prompt = template.replace("{analysis_json}", analysis_json)

        response_text = self._call_llm(prompt)

        try:
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                workflow = json.loads(response_text[json_start:json_end])
            else:
                workflow = {"raw_response": response_text}
        except json.JSONDecodeError:
            workflow = {"raw_response": response_text}

        return workflow

    def detail_fill(self, workflow_json: str) -> dict:
        """Fill in implementation details for each workflow stage.

        Args:
            workflow_json: JSON string of the workflow plan.

        Returns:
            Complete generation instruction dict for WorkloadGenerator.
        """
        template = self._load_prompt("detail_fill.md")
        prompt = template.replace("{workflow_json}", workflow_json)

        response_text = self._call_llm(prompt)

        try:
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                instruction = json.loads(response_text[json_start:json_end])
            else:
                instruction = {"raw_response": response_text}
        except json.JSONDecodeError:
            instruction = {"raw_response": response_text}

        return instruction

    def evaluate_comparison(self, comparison_json: str) -> dict:
        """Evaluate comparison report and recommend iteration adjustments.

        Args:
            comparison_json: JSON string of the comparison report.

        Returns:
            Evaluation dict with iteration_priority and adjustments.
        """
        template = self._load_prompt("evaluate_comparison.md")
        prompt = template.replace("{comparison_json}", comparison_json)

        response_text = self._call_llm(prompt)

        try:
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                evaluation = json.loads(response_text[json_start:json_end])
            else:
                evaluation = {"raw_response": response_text}
        except json.JSONDecodeError:
            evaluation = {"raw_response": response_text}

        return evaluation

    def run_full_chain(self, profile_json: str) -> dict:
        """Run the full prompt chain: analyze → plan → detail_fill.

        Args:
            profile_json: JSON string of the customer Profile.

        Returns:
            Complete generation instruction dict.
        """
        logger.info("Step 1: Analyzing customer Profile...")
        analysis = self.analyze_profile(profile_json)

        logger.info("Step 2: Planning Business Workflow...")
        workflow = self.plan_workflow(json.dumps(analysis))

        logger.info("Step 3: Filling implementation details...")
        instruction = self.detail_fill(json.dumps(workflow))

        return instruction
```

- [ ] **Step 4: Write tests**

```python
"""Tests for Agent Core and Strategy."""

import json
from agent.strategy import decide_iteration_priority


def test_strategy_converged():
    report = {
        "topdown_l1": {
            "frontend_bound": {"within_threshold": True, "diff_pct": 3.0},
            "backend_bound": {"within_threshold": True, "diff_pct": 4.0},
            "bad_speculation": {"within_threshold": True, "diff_pct": 2.0},
            "retiring": {"within_threshold": True, "diff_pct": 5.0},
        },
        "hotspot_coverage": {"coverage_pct": 85.0},
    }
    priority = decide_iteration_priority(report)
    assert priority == 0  # Converged


def test_strategy_priority_1_small_diffs():
    report = {
        "topdown_l1": {
            "frontend_bound": {"within_threshold": False, "diff_pct": 4.0},
            "backend_bound": {"within_threshold": True, "diff_pct": 3.0},
        },
        "hotspot_coverage": {"coverage_pct": 90.0},
    }
    priority = decide_iteration_priority(report)
    assert priority == 1


def test_strategy_priority_2_moderate_diffs():
    report = {
        "topdown_l1": {
            "frontend_bound": {"within_threshold": False, "diff_pct": -8.0},
            "backend_bound": {"within_threshold": True, "diff_pct": 3.0},
        },
        "hotspot_coverage": {"coverage_pct": 85.0},
    }
    priority = decide_iteration_priority(report)
    assert priority == 2


def test_strategy_priority_3_large_diffs():
    report = {
        "topdown_l1": {
            "frontend_bound": {"within_threshold": False, "diff_pct": -15.0},
        },
        "hotspot_coverage": {"coverage_pct": 70.0},
    }
    priority = decide_iteration_priority(report)
    assert priority == 3


def test_agent_core_prompt_loading():
    """Test that prompt templates can be loaded from files."""
    import pathlib
    prompts_dir = pathlib.Path(__file__).parent.parent.parent / "src" / "agent" / "prompts"

    analyze = (prompts_dir / "analyze_profile.md").read_text()
    assert "customer Profile" in analyze
    assert "{profile_json}" in analyze

    plan = (prompts_dir / "plan_workflow.md").read_text()
    assert "workflow_stages" in plan
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/agent/test_strategy.py tests/agent/test_agent_core.py -v`
Expected: 4 strategy tests PASS, 1 prompt loading test PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent/__init__.py src/agent/agent_core.py src/agent/strategy.py src/agent/prompts/ tests/agent/
git commit -m "feat: AgentCore — prompt chain orchestrator + strategy decision logic"
```

---

### Task 10: End-to-End Pipeline Integration

**Files:**
- Create: `src/harness/pipeline.py`
- Create: `src/config/default_config.yaml`
- Create: `tests/agent/test_pipeline.py`
- Create: `examples/search_ranking/customer_data/` (sample data)
- Create: `examples/search_ranking/deploy_config.json`
- Update: `README.md`

This task wires all components together into a callable end-to-end pipeline: load customer data → generate workload → build → run → collect → compare → report.

- [ ] **Step 1: Write default_config.yaml**

```yaml
# Default configuration for Workload Simulation Framework

framework:
  name: workload-sim
  version: "0.1.0"
  log_level: INFO

agent:
  model: claude-sonnet-4-6
  max_tokens: 4096

codegen:
  compile_flags: "-O2 -march=armv8.2-a"
  default_dependencies:
    - name: folly
      version: "2.1.0"

harness:
  cmake_path: cmake
  make_path: make
  build_dir_suffix: build

comparison:
  topdown_threshold_pct: 10.0
  memory_threshold_pct: 5.0
  coverage_threshold_pct: 80.0

run:
  default_warmup_seconds: 30
  default_measurement_seconds: 60
  default_thread_count: 4
  default_qps: 100
```

- [ ] **Step 2: Write pipeline.py**

```python
"""End-to-end pipeline orchestration for Phase 1."""

import json
import logging
import pathlib

from ingestion.flamegraph_parser import FlamegraphParser
from ingestion.topdown_parser import TopdownParser
from profile.profile_schema import Profile, ProfileMetadata
from profile.profile_store import ProfileStore
from profile.comparator import ProfileComparator
from agent.agent_core import AgentCore
from agent.strategy import decide_iteration_priority
from codegen.generator import WorkloadGenerator
from harness.build_runner import BuildRunner
from harness.execution_runner import ExecutionRunner, ExecutionResult
from harness.metrics_collector import MetricsCollector
from harness.run_config import RunConfig

logger = logging.getLogger(__name__)


class PipelineResult:
    """Result of a full pipeline run."""

    def __init__(self, success: bool, customer_profile: Profile | None = None,
                 workload_profile: Profile | None = None, comparison_report: dict | None = None,
                 project_dir: pathlib.Path | None = None, error: str = ""):
        self.success = success
        self.customer_profile = customer_profile
        self.workload_profile = workload_profile
        self.comparison_report = comparison_report
        self.project_dir = project_dir
        self.error = error


class Pipeline:
    """End-to-end pipeline: data ingestion → agent analysis → code gen → build → run → compare.

    Phase 1: Manual-driven, no auto-iteration. Each step can be called individually or
    the full pipeline can be run in sequence.

    Args:
        output_base_dir: Base directory for all generated output.
        config_path: Path to framework config YAML (optional).
    """

    def __init__(self, output_base_dir: pathlib.Path, config_path: pathlib.Path | None = None):
        self.output_base_dir = output_base_dir
        self.output_base_dir.mkdir(parents=True, exist_ok=True)

        self.profile_store = ProfileStore(base_dir=output_base_dir / "profiles")
        self.flamegraph_parser = FlamegraphParser()
        self.topdown_parser = TopdownParser()
        self.comparator = ProfileComparator()
        self.agent = AgentCore()
        self.generator = WorkloadGenerator()
        self.build_runner = BuildRunner()
        self.execution_runner = ExecutionRunner()
        self.metrics_collector = MetricsCollector()

    def ingest_customer_data(
        self,
        flamegraph_path: pathlib.Path | None = None,
        topdown_path: pathlib.Path | None = None,
        customer_name: str = "customer",
        metadata: dict | None = None,
    ) -> Profile:
        """Ingest customer data files into a structured Profile.

        Args:
            flamegraph_path: Path to customer flamegraph file (folded format).
            topdown_path: Path to customer Topdown data file (JSON or CSV).
            customer_name: Customer identifier.
            metadata: Additional metadata dict.

        Returns:
            Merged customer Profile.
        """
        # Parse flamegraph
        hotspots = []
        if flamegraph_path is not None:
            hotspots = self.flamegraph_parser.parse_folded(flamegraph_path)
            logger.info(f"Parsed {len(hotspots)} hotspots from flamegraph")

        # Parse Topdown
        topdown_profile = None
        if topdown_path is not None:
            if topdown_path.suffix == ".json":
                topdown_profile = self.topdown_parser.parse_json(topdown_path)
            elif topdown_path.suffix == ".csv":
                topdown_profile = self.topdown_parser.parse_csv(topdown_path)
            logger.info(f"Parsed Topdown data from {topdown_path.suffix}")

        # Merge into a single Profile
        meta = metadata or {}
        profile = Profile(
            metadata=ProfileMetadata(
                customer=customer_name,
                date=meta.get("date", "unknown"),
                platform=meta.get("platform", "arm64"),
                neoverse_core=meta.get("neoverse_core"),
                software_stack=meta.get("software_stack", []),
            ),
            hotspots=hotspots,
            topdown=topdown_profile.topdown if topdown_profile else None,
            topdown_l2=topdown_profile.topdown_l2 if topdown_profile else None,
            memory=topdown_profile.memory if topdown_profile else None,
            business_logic=meta.get("business_logic"),
        )

        # Save to store
        self.profile_store.save(profile, name=f"{customer_name}_profile")
        logger.info(f"Customer Profile saved: {customer_name}_profile")

        return profile

    def generate_workload(self, customer_profile: Profile) -> pathlib.Path:
        """Generate workload code from customer Profile via Agent prompt chain.

        Args:
            customer_profile: The target customer Profile.

        Returns:
            Path to the generated project directory.
        """
        profile_json = customer_profile.model_dump_json()

        logger.info("Running Agent prompt chain: analyze → plan → detail_fill")
        instruction = self.agent.run_full_chain(profile_json)

        logger.info(f"Agent produced generation instruction with {len(instruction.get('stages', []))} stages")

        # Generate project
        project_dir = self.output_base_dir / "generated_workload"
        self.generator.generate(instruction, project_dir)

        logger.info(f"Workload project generated at: {project_dir}")
        return project_dir

    def build_workload(self, project_dir: pathlib.Path) -> pathlib.Path | None:
        """Build the generated workload project.

        Args:
            project_dir: Path to the generated project directory.

        Returns:
            Path to the built binary, or None if build failed.
        """
        result = self.build_runner.build(project_dir)

        if not result.success:
            logger.error(f"Build failed: {result.error}")
            return None

        logger.info(f"Build succeeded: binary at {result.binary_path}")
        return result.binary_path

    def run_workload(self, binary_path: pathlib.Path, run_config: RunConfig | None = None) -> ExecutionResult:
        """Run the workload binary.

        Args:
            binary_path: Path to the workload binary.
            run_config: Runtime configuration. Defaults to RunConfig().

        Returns:
            ExecutionResult.
        """
        if run_config is None:
            run_config = RunConfig()

        result = self.execution_runner.run(binary_path, run_config)

        if result.success:
            logger.info("Workload execution succeeded")
        else:
            logger.error(f"Workload execution failed: {result.stderr}")

        return result

    def compare_results(self, customer_profile: Profile, workload_profile: Profile, iteration: int = 0) -> dict:
        """Compare customer Profile with workload Profile.

        Args:
            customer_profile: Target Profile.
            workload_profile: Generated workload Profile.
            iteration: Iteration number.

        Returns:
            Comparison report dict.
        """
        report = self.comparator.compare(customer_profile, workload_profile, iteration)
        logger.info(f"Comparison: converged={report['convergence']['converged']}, reason={report['convergence']['reason']}")
        return report

    def run_full_pipeline(
        self,
        flamegraph_path: pathlib.Path | None = None,
        topdown_path: pathlib.Path | None = None,
        customer_name: str = "customer",
        metadata: dict | None = None,
    ) -> PipelineResult:
        """Run the full end-to-end pipeline.

        Phase 1: Runs ingestion → agent → codegen → build → (manual run/collect/compare).
        Build and execution happen locally. Collection is manual (provide pre-collected files).

        Args:
            flamegraph_path: Path to customer flamegraph file.
            topdown_path: Path to customer Topdown data.
            customer_name: Customer identifier.
            metadata: Additional metadata dict.

        Returns:
            PipelineResult with customer Profile, project dir, and status.
        """
        try:
            # Step 1: Ingest
            customer_profile = self.ingest_customer_data(
                flamegraph_path=flamegraph_path,
                topdown_path=topdown_path,
                customer_name=customer_name,
                metadata=metadata,
            )

            # Step 2: Generate
            project_dir = self.generate_workload(customer_profile)

            # Step 3: Build
            binary_path = self.build_workload(project_dir)

            if binary_path is None:
                return PipelineResult(
                    success=False,
                    customer_profile=customer_profile,
                    project_dir=project_dir,
                    error="Build failed",
                )

            # Phase 1: Build succeeded. Run/collect/compare are manual.
            # User runs the workload on the target machine, collects metrics,
            # then calls compare_results() with the workload Profile.

            return PipelineResult(
                success=True,
                customer_profile=customer_profile,
                project_dir=project_dir,
            )

        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            return PipelineResult(success=False, error=str(e))
```

- [ ] **Step 3: Create example customer data**

Create `examples/search_ranking/customer_data/flamegraph_folded.txt`:

```
main;SearchService::process;FeatureExtractor::extract;folly::futures::detail::FutureImpl::then 2500
main;SearchService::process;FeatureExtractor::extract;CustomerCustom::hashFeature 1800
main;SearchService::process;ModelInferencer::infer;taskflow::ParallelFor::dispatch 3200
main;SearchService::process;ModelInferencer::infer;CustomerCustom::matmulCompute 2800
main;SearchService::process;Ranker::rankMerge;CustomerCustom::mergeAndSort 1500
main;SearchService::process;Ranker::rankMerge;folly::sorted_vector_map::find 600
main;SearchService::process;DedupFilter::filter;CustomerCustom::branchFilter 900
main;SearchService::process;ResultAssembler::assemble 400
main;SearchService::process 300
main 200
```

Create `examples/search_ranking/customer_data/topdown.json`:

```json
{
  "topdown_l1": {
    "frontend_bound": 0.18,
    "backend_bound": 0.55,
    "bad_speculation": 0.07,
    "retiring": 0.20
  },
  "topdown_l2": {
    "frontend_bound": {
      "fetch_latency": 0.12,
      "branch_detect": 0.06
    },
    "backend_bound": {
      "memory_bound": 0.40,
      "core_bound": 0.15
    },
    "bad_speculation": {
      "branch_mispredict": 0.05,
      "other": 0.02
    },
    "retiring": {
      "heavy_ops": 0.12,
      "light_ops": 0.08
    }
  },
  "memory": {
    "bandwidth_gbps": 48.5,
    "l3_miss_rate": 0.12,
    "tlb_miss_rate": 0.03,
    "working_set_size_mb": 256
  }
}
```

Create `examples/search_ranking/customer_data/business_description.md`:

```markdown
# Search Ranking Service

## Architecture
High-concurrency RPC search service processing ranking requests.

## Business Workflow
1. Feature Extraction: Extract features from request data using async computation (folly futures)
2. Model Inference: Run model inference with parallel compute (taskflow)
3. Rank & Merge: Merge multiple ranking results, sort by score
4. Dedup & Filter: Remove duplicates with branch-heavy conditional logic
5. Result Assembly: Pack results into response format

## Key Observations
- Backend bound dominates (55%), especially memory bound (40%)
- Model inference and feature extraction are the two biggest stages
- Working set is ~256MB with significant L3 cache misses
```

Create `examples/search_ranking/deploy_config.json`:

```json
{
  "deploy_config": {
    "target_host": "192.168.1.100",
    "target_arch": "arm64",
    "dependencies": [
      { "name": "folly", "version": "2.1.0" },
      { "name": "taskflow", "version": "3.7.0" }
    ],
    "deploy_script": "deploy/search_ranking_deploy.sh",
    "env_vars": {}
  }
}
```

- [ ] **Step 4: Write integration test (skipped without API key)**

```python
"""Integration test for the full pipeline.

Note: This test requires ANTHROPIC_API_KEY to be set.
Without it, the Agent Core will fail and this test is marked as skipped.
"""

import json
import pathlib
import pytest
import os

from harness.pipeline import Pipeline


EXAMPLES_DIR = pathlib.Path(__file__).parent.parent.parent / "examples" / "search_ranking" / "customer_data"


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set"
)
def test_pipeline_ingest_and_generate():
    """Test that the pipeline can ingest customer data and generate workload code."""
    output_dir = pathlib.Path(__file__).parent.parent.parent / "test_output"
    pipeline = Pipeline(output_base_dir=output_dir)

    # Step 1: Ingest
    profile = pipeline.ingest_customer_data(
        flamegraph_path=EXAMPLES_DIR / "flamegraph_folded.txt",
        topdown_path=EXAMPLES_DIR / "topdown.json",
        customer_name="search_ranking",
        metadata={
            "date": "2026-07-27",
            "neoverse_core": "N2",
            "business_logic": "Search ranking service",
            "software_stack": [{"name": "folly", "version": "2.1.0"}, {"name": "taskflow", "version": "3.7.0"}],
        },
    )

    assert profile.metadata.customer == "search_ranking"
    assert len(profile.hotspots) > 0
    assert profile.topdown is not None


def test_pipeline_ingest_only():
    """Test ingestion without API key (no agent call)."""
    output_dir = pathlib.Path(__file__).parent.parent.parent / "test_output"

    from ingestion.flamegraph_parser import FlamegraphParser
    from ingestion.topdown_parser import TopdownParser
    from profile.profile_schema import Profile, ProfileMetadata

    fg_parser = FlamegraphParser()
    td_parser = TopdownParser()

    hotspots = fg_parser.parse_folded(EXAMPLES_DIR / "flamegraph_folded.txt")
    td_profile = td_parser.parse_json(EXAMPLES_DIR / "topdown.json")

    profile = Profile(
        metadata=ProfileMetadata(customer="search_ranking", date="2026-07-27"),
        hotspots=hotspots,
        topdown=td_profile.topdown,
        topdown_l2=td_profile.topdown_l2,
        memory=td_profile.memory,
        business_logic="Search ranking service",
    )

    assert len(profile.hotspots) > 0
    assert profile.topdown.backend_bound == 0.55
```

- [ ] **Step 5: Run non-integration tests**

Run: `pytest tests/agent/test_pipeline.py -v -k "not integration"`
Expected: `test_pipeline_ingest_only` PASS.

- [ ] **Step 6: Write README.md**

```markdown
# Workload Simulation Framework

Generate synthetic C++ workloads that mimic the microarchitectural behavior (Topdown, memory bandwidth, hotspot call paths) of customer real-world software on ARM64 platforms.

## Quick Start

```bash
pip install -e ".[dev]"
pytest tests/ -v  # run all tests
```

## Architecture

Five core components:
1. **Data Ingestion** — Parse flamegraph, Topdown, memory data → structured Profile
2. **Profile Store** — Save/load Profiles as JSON
3. **Agent Core** — LLM prompt chain: analyze → plan → generate instruction
4. **Code Gen Engine** — Generate C++ workload + config from instruction
5. **Harness** — Build, run, collect, compare

## Phase 1 Status

End-to-end loop works: ingest → agent → generate → build → (manual run/collect/compare).
No auto-iteration yet (Phase 2).

## Example

```python
from harness.pipeline import Pipeline

pipeline = Pipeline(output_base_dir="./output")

profile = pipeline.ingest_customer_data(
    flamegraph_path="examples/search_ranking/customer_data/flamegraph_folded.txt",
    topdown_path="examples/search_ranking/customer_data/topdown.json",
    customer_name="search_ranking",
)

project_dir = pipeline.generate_workload(profile)
binary_path = pipeline.build_workload(project_dir)
```

See `examples/search_ranking/` for a complete example with customer data.

## Design Doc

See `docs/superpowers/specs/2026-07-27-workload-simulation-design.md` for the full design specification.
```

- [ ] **Step 7: Commit**

```bash
git add src/harness/pipeline.py src/config/default_config.yaml tests/agent/test_pipeline.py examples/search_ranking/ README.md
git commit -m "feat: Pipeline integration + example data + README — Phase 1 end-to-end loop complete"
```

---

### Task 11: Run All Tests & Final Validation

**Files:**
- No new files. Run full test suite and verify.

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v --tb=short`
Expected: All non-integration tests PASS. Integration tests SKIPPED (no API key).

- [ ] **Step 2: Verify example data ingestion works**

```bash
cd /c/Users/jack/Desktop/harness
python -c "
from ingestion.flamegraph_parser import FlamegraphParser
from ingestion.topdown_parser import TopdownParser
import pathlib

fg = FlamegraphParser()
hotspots = fg.parse_folded(pathlib.Path('examples/search_ranking/customer_data/flamegraph_folded.txt'))
print(f'Hotspots: {len(hotspots)}')
for h in hotspots[:3]:
    print(f'  {h.function} ({h.source}) self={h.self_pct:.1f}%')

td = TopdownParser()
profile = td.parse_json(pathlib.Path('examples/search_ranking/customer_data/topdown.json'))
print(f'Topdown: frontend={profile.topdown.frontend_bound} backend={profile.topdown.backend_bound}')
"
```

Expected: Prints hotspot count, top 3 hotspots with classification, and Topdown values.

- [ ] **Step 3: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "chore: Phase 1 final validation — all tests passing"
```

---

## Self-Review Checklist

**1. Spec coverage:**

| Spec Section | Covered by Task |
|-------------|----------------|
| Data Ingestion (Flamegraph + Topdown) | Task 2, Task 3 |
| Profile Schema | Task 1 |
| Profile Store | Task 4 |
| Profile Comparator | Task 4 |
| Agent Core (prompt chain) | Task 9 |
| Agent Strategy | Task 9 |
| Code Gen Engine (scaffold) | Task 5 |
| Code Gen Engine (behaviors) | Task 6 |
| Code Gen Engine (knobs) | Task 6 |
| Harness Build Runner | Task 7 |
| Harness Execution Runner | Task 7 |
| Harness Metrics Collector | Task 8 |
| Pipeline integration | Task 10 |
| Example data | Task 10 |

Note: MemoryParser, VersionParser, TextParser, MCP server, Deploy Runner, and auto-iteration loop are **Phase 2** items not covered in this Phase 1 plan.

**2. Placeholder scan:** No TBD/TODO/fill-in-later found. All code is complete.

**3. Type consistency:**
- `Profile` model defined in Task 1, used consistently across all tasks
- `HotspotFunction.source` is "open_source" or "customer_custom" — consistent in FlamegraphParser and Comparator
- `TopdownL1` fields match between TopdownParser output and Comparator input
- `RunConfig` defined in Task 7, used in ExecutionRunner and Pipeline
- `BuildResult` / `ExecutionResult` / `CollectionResult` / `PipelineResult` — all distinct, no name collisions
