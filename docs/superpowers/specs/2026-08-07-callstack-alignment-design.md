# Call-Stack Structural Alignment Design (Front A)

> Date: 2026-08-07
> Status: Draft
> Parent spec: [2026-07-27-workload-simulation-design.md](./2026-07-27-workload-simulation-design.md)
> Scope: Front A — improve mirage's ability to replicate customer application
> workloads **from source code** by aligning the **call-stack structure** of the
> generated C++ workload to the customer flamegraph.

## 1. Problem Statement

### Context

mirage's stated headline goal is **open-source call-stack alignment**: the
generated workload's flamegraph should mirror the customer's call tree at the
open-source library layer, while customer-custom code is synthesized for
microarchitectural alignment. The current Phase-1 implementation does not
deliver this:

- `FlamegraphParser` already parses each hotspot's full `call_path`
  ([flamegraph_parser.py](../../src/ingestion/flamegraph_parser.py)), but codegen
  **discards it**. `main.cpp` flat-loops `stage_compute()` once per stage, so
  the generated flamegraph is `main→stage_compute` — nothing like the
  customer's call tree.
- `direct_call` is a placeholder: `{{ call_statement }}` is filled entirely by
  the LLM with no validation; open-source frames rarely appear for real.
- `weight_pct` is collected but ignored; hotspot time allocation is wrong.
- `run_full_pipeline` stops at `build`; run/collect/compare are not wired, so
  structural alignment cannot even be measured.

### Goal

Make the generated C++ workload's flamegraph **structurally resemble** the
customer's: replicate the trunk + stage skeleton, emit **real** open-source
library calls at the correct caller/callee positions, collapse customer-custom
subtrees into named synthesis functions, and allocate per-node time from the
customer's own self-samples so the flamegraph matches at every level. Validate
on ARM Neoverse with real `perf`/devkit collection.

### Success Criteria

| Metric | Target |
|--------|--------|
| Call-path structural overlap (trunk + stage + open-source leaves) | > 80% |
| Per-level self% ratio match (generated vs customer) | qualitative match, reported |
| Open-source leaf structural coverage | > 80% |
| End-to-end single-pass run→collect→compare on ARM | works |

### Scope & Non-goals

**In scope (Front A):**
- `CallTreeBuilder`: deterministic call-tree derivation from `call_path`.
- Layer 1/2 service skeleton generation (the missing nested trunk).
- Real open-source calls via hybrid catalog + LLM.
- Per-node self-time budgets; request-driven tree traversal runner.
- `StructuralComparator` (call-path overlap).
- Pipeline wiring of a single-pass `run→collect→compare` (ARM real path).

**Out of scope (later specs):**
- Front B — Topdown L1/L2 → behavior-parameter **search/tuning**. (We ground
  synthesis in customer data and infer behavior archetypes from function names,
  but we do not run a Topdown-target-driven parameter search.)
- Front C — Agent auto-iteration closed loop (`compare→adjust→regen`). This
  spec wires a **single pass** collect+compare only.
- Multi-module distributed deploy, paired stress program, Go/Java targets.

**Incidental fixes (basic correctness, not Front B):** behavior templates will
honor `compute_type`/`access_pattern` (currently ignored) and pick
behaviorally-appropriate kernels by name.

---

## 2. Core Principle

> **The call-tree skeleton is derived deterministically from the customer's
> `call_path` (ground truth), not invented by the LLM.** The LLM only fills
> leaf behavior details (open-source `call_statement` via catalog-first, and
> custom synthesis config). Structure comes from data; this maximizes
> structural fidelity and minimizes LLM unreliability for the skeleton itself.

Nothing is hardcoded to specific libraries. `CallTreeBuilder` is
library-agnostic and shape-agnostic (any depth, any branching). The
open-source API catalog is **YAML data**; adding a library or function is a
data edit, not a code change. Catalog misses fall back to LLM + build-retry and
cache successful results back into the catalog.

---

## 3. Architecture & Data Flow

```text
ingest: FlamegraphParser → hotspots (with call_path) + self_samples   ── existing
                          ↓
   CallTreeBuilder (NEW) ── merge all call_paths → CallTree
     ├─ trunk (common prefix): main → SearchService::process
     ├─ stage nodes: FeatureExtractor::extract / ModelInferencer::infer / ...
     └─ leaves, classified via catalog:
         · open_source  → real call (catalog hit / LLM + build-retry)
         · customer_custom → collapse maximal custom subtrees → custom_synth
                          ↓ SkeletonDescriptor (nested JSON, internal IR)
   WorkloadGenerator (EXTEND):
     ├─ ServiceSkeletonGen (NEW, Layer1/2) ← trunk → nested struct/methods
     ├─ BehaviorGen (EXTEND, Layer3)        ← leaves → real calls + synthesis
     └─ main.cpp: request-driven tree-traversal runner (per-node self budgets)
                          ↓ generated C++ project
   Harness (ARM real path): build → run(warmup+measure, thread_count) →
     perf record (flamegraph) + devkit (topdown)
                          ↓ workload folded flamegraph + topdown
   StructuralComparator (NEW) ← customer call_paths vs workload call_paths
     └─ call-path overlap + ProfileComparator (topdown/memory/coverage) → report
                          ↓ report (single pass; auto-iteration is Front C)
```

---

## 4. Data Models

### 4.1 CallTree / SkeletonDescriptor (internal IR, produced by CallTreeBuilder)

Nested tree. Node identity = **full path** (not function name), so the same
function under different parents is distinct. Kinds: `trunk`, `stage`,
`open_source_leaf`, `custom_synth`.

Collapse rule (general, preserves skeleton): **walk the merged tree; any
maximal customer-custom subtree with no open-source descendant is collapsed
into one `custom_synth` node** (named after its root or the enclosing stage,
`self_pct` = sum of the subtree's leaf self%). Customer-custom nodes that lie
on a path to an open-source leaf are kept as `stage`/`trunk` skeleton nodes.
Open-source frames are preserved as `open_source_leaf` (their internal callees
come for free from the real call).

```json
{
  "project_name": "search_ranking_sim",
  "trunk": ["main", "SearchService::process"],
  "service_node": "SearchService::process",
  "root": {
    "function": "main",
    "node_kind": "trunk",
    "self_pct": 8.5,
    "self_work": { "kind": "self_budget", "archetype": "compute", "units": 200 },
    "children": [
      {
        "function": "SearchService::process",
        "stage_class": "SearchService", "stage_method": "process",
        "node_kind": "trunk",
        "self_pct": 12.8,
        "self_work": { "kind": "self_budget", "archetype": "compute", "units": 300 },
        "children": [
          {
            "function": "FeatureExtractor::extract",
            "stage_class": "FeatureExtractor", "stage_method": "extract",
            "node_kind": "stage",
            "self_pct": 0.0,
            "children": [
              {
                "function": "folly::futures::detail::FutureImpl::then",
                "node_kind": "open_source_leaf",
                "library": "folly",
                "self_pct": 10.6,
                "self_work": { "kind": "real_call" },
                "call_spec": {
                  "includes": ["<folly/futures/Future.h>"],
                  "statement": "folly::makeFuture(42).then([](int x){ return x+1; }).wait();",
                  "setup": ""
                }
              },
              {
                "function": "FeatureExtractor::custom_synth",
                "node_kind": "custom_synth",
                "self_pct": 7.4,
                "self_work": {
                  "kind": "synthesis",
                  "archetype": "hash",
                  "config": { "compute_type": "hash", "iterations": 100 }
                }
              }
            ]
          }
        ]
      }
    ]
  },
  "config": {
    "thread_count": 8, "qps": 1000,
    "warmup_seconds": 30, "measurement_seconds": 60
  }
}
```

- `self_pct`: from customer self_samples / total_samples, for **every** node
  (interior nodes included — see §6a).
- `self_work.units`: proportional work amount (∝ self_samples) sizing the
  calibrated loop. The constant scales total runtime; **ratios are preserved**.
- `archetype`: behavior kernel class (see §6c).

### 4.2 OpenSourceAPICatalog (`src/config/open_source_api_catalog.yaml`)

Extends the existing `open_source_libraries.yaml` classifier with buildable
call specs. Keys are **demangled** function names.

```yaml
libraries:
  folly:
    functions:
      "folly::futures::detail::FutureImpl::then":
        includes: ["<folly/futures/Future.h>"]
        call_statement: "folly::makeFuture(42).then([](int x){ return x+1; }).wait();"
        setup: ""            # optional one-time init
        min_version: "2.1.0"
      "folly::sorted_vector_map::find":
        includes: ["<folly/sorted_vector_map.h>"]
        call_statement: "folly::sorted_vector_map<int,int> m; m.find(0);"
        setup: ""
  taskflow:
    functions:
      "tf::ParallelFor::dispatch":
        includes: ["<taskflow/algorithm/for_each.hpp>"]
        call_statement: "/* representative call */"
        setup: "tf::Taskflow tf; tf::for_each(...);"
# default: miss → LLM generates call_statement → build-validate → cache back
```

### 4.3 Structural Alignment Metric

**Call-path structural overlap** = |covered required frames| / |required
frames|, where a required frame is a `(frame, depth, parent_frame)` triple from
the customer stacks, restricted to **trunk + stage + open_source_leaf**
frames (custom frames are expected divergence, excluded).

A required triple is *covered* iff some workload stack has the same frame at
the same depth with the same parent.

Reported sub-metrics: `trunk_present`, `stage_coverage_pct`,
`open_source_structural_coverage_pct`, `overall_overlap_pct`. Merged into the
comparison report under `structural_alignment`;纳入收敛判定 (target > 80%).

---

## 5. Components

| Component | Location | Responsibility |
|-----------|----------|----------------|
| `CallTreeBuilder` (NEW) | `src/codegen/call_tree.py` | `build(hotspots, profile) -> SkeletonDescriptor`. Merge stacks → tree; detect trunk/stage; collapse custom subtrees; classify leaves via catalog; compute self_pct/self_work for every node. Pure deterministic, no LLM. |
| `CallTree`/`SkeletonDescriptor` models (NEW) | `src/codegen/call_tree.py` | pydantic models for the nested IR. |
| `OpenSourceAPICatalog` (NEW) | `src/codegen/catalog.py` + `src/config/open_source_api_catalog.yaml` | `lookup(function) -> CallSpec \| None`; `record_fallback(function, spec)` caches LLM results. Reuses classifier regex for library identification. |
| `ServiceSkeletonGen` (NEW) | `src/codegen/skeleton_gen.py` + `templates/service/` | Layer 1/2: render `main.cpp` (request-driven runner) + `service.{h,cpp}.j2` (nested trunk, `SearchService::process` calls each stage) + per-stage `Stage` class files. All stage/service methods `__attribute__((noinline))`. |
| `BehaviorGen` (EXTEND) | `src/codegen/behavior_gen.py` + `templates/behaviors/` | Render leaves: open-source → real `call_spec.statement` (catalog-first); custom → `custom_synth` synthesis. Fix `compute_type`/`access_pattern` honored; archetype-driven kernels. |
| `WorkloadGenerator` (EXTEND) | `src/codegen/generator.py` | `generate()`: `CallTreeBuilder.build()` → `ServiceSkeletonGen` + `BehaviorGen` + `KnobGen`. |
| `StructuralComparator` (NEW) | `src/profile/structural_comparator.py` | `compare(customer_stacks, workload_stacks) -> structural_alignment` report. New `FlamegraphParser.parse_stacks()` returns all `(frames, count)` lines. |
| `MetricsCollector` (EXTEND) | `src/harness/metrics_collector.py` | `collect_flamegraph()`: `perf record` → `perf script` → stack-collapse to folded format (ARM). |
| `Pipeline` (EXTEND) | `src/harness/pipeline.py` | `run_and_compare()`: build → run(warmup+measure, thread_count) → collect(devkit topdown + perf flamegraph) → parse workload profile → `ProfileComparator` + `StructuralComparator` → merged report. Single pass. |
| Agent prompts (EXTEND) | `src/agent/prompts/detail_fill.md` | Now fills only leaf behavior details (open-source `call_statement` when catalog misses; custom synthesis config). Skeleton structure is no longer the LLM's job. |

---

## 6. Realism Fidelity Levers

**(a) Per-node self-time budget (core).** Every node — trunk, stage, leaf —
gets `self_work` proportional to the customer's self_samples for that frame
(the parser already captures interior self-time). The generated node does a
calibrated micro-loop of that size. The flamegraph self% then matches at
**every level**, not just leaves.

**(b) Request-driven tree traversal (replaces flat weight dispatch).** The
runner loops "requests"; each request walks the full call tree
(`main→process→stages→leaves`), each node doing its self-budget then recursing.
The tree shape and per-level widths emerge naturally; perf samples ∝ time ∝
work, so self% ratios align by construction. Stages not present in every
customer request are traversed with probability equal to their cumulative-sample
share, so their flamegraph width matches the customer's.

**(c) Name-driven behavior archetypes (not fixed sin/cos).** Custom synthesis
picks a kernel by function-name keywords via an extensible YAML map
(`matmul`/`multiply`→matrix multiply, `hash`→hashing, `sort`/`merge`→sort,
`branch`/`filter`→branch-heavy, `lookup`/`access`→random memory access),
falling back to the stage's Topdown (memory_bound-dominant → memory, else
compute) when the name is uninformative. Realistic per-leaf behavior without
Topdown-target tuning.

**(d) Synthesis grounded in customer data.** Memory synthesis `working_set_mb`
comes from `profile.memory.working_set_size_mb`; access pattern inferred from
`topdown_l2.backend_bound.memory_bound` share (high → random). No made-up 64MB.

**(e) `noinline` to preserve frames (critical, cheap).** Generated trunk/stage
methods are `__attribute__((noinline))` (workload also compiled with
`-fno-inline-small-functions`). Customer frames that appear in their flamegraph
were not inlined in their build; we match by not inlining. Without this, stage
frames fold into callers and structural alignment silently breaks.

**(f) Replicate thread concurrency.** Runner spawns `thread_count` threads
(from config) each running the request loop. The aggregated flamegraph shape is
preserved while memory pressure becomes realistic (needed for plausible
backend_bound / bandwidth). Full Topdown-value tuning stays in Front B.

**(g) Open-source internal subtrees for free.** Real calls to
`folly::Future::then` pull folly's own internal call tree into the generated
flamegraph identically to the customer's. This is the core value of "real call"
over synthesis.

**(h) Symbol demangle normalization.** Folded perf output may be mangled; a
demangle step normalizes names so catalog keys (demangled) match flamegraph
frames.

> Boundary: (a)(b)(e)(f) are flamegraph structure/time fidelity (Front A);
> (c)(d) are behavior grounding (Front A, not Topdown target tuning).
> Topdown L1/L2→parameter search (Front B) and Agent auto-iteration (Front C)
> remain out of scope.

---

## 7. Pipeline Wiring (single pass)

```python
Pipeline.run_and_compare(customer_profile, project_dir):
    binary = build_workload(project_dir)            # ARM target
    exec   = execution_runner.run(binary, run_config)   # warmup + measure, thread_count
    td     = metrics_collector.collect_topdown(...)      # devkit
    fg     = metrics_collector.collect_flamegraph(...)   # perf record → folded
    workload_profile = parse(fg, td)                     # FlamegraphParser + TopdownParser
    report = {
        ...ProfileComparator.compare(customer, workload),
        "structural_alignment": StructuralComparator.compare(customer_stacks, workload_stacks)
    }
    return report
```

`run_full_pipeline` is extended to call `run_and_compare` after `build` when an
ARM/devkit environment is configured (gated on `config.harness.devkit_cmd`);
otherwise it retains the current build-only behavior (dev machines without
devkit). No auto-adjust loop (Front C).

---

## 8. Error Handling

| Scenario | Response | Limit |
|----------|----------|-------|
| Catalog misses an open-source function | LLM generates `call_statement` → ARM build-validate → on failure, LLM repairs | 3 retries |
| Open-source lib not installed / version mismatch on target | Build fails → report flags `missing_lib` (auto-handling in Front C) | — |
| Workload flamegraph empty (collection failed) | structural overlap = 0, reason `empty_workload_flamegraph` | — |
| No recognizable trunk (no common prefix) | Degrade: `trunk = [main]`, align at stage level only | — |
| Same function under multiple parents | Distinct path-keyed nodes; no mis-merge | — |
| Customer name uninformative for archetype | Fall back to stage Topdown (memory-bound dominant → memory; else compute) | — |

---

## 9. Testing

- `CallTreeBuilder`: stack merge, trunk detection, maximal-custom-subtree
  collapse, leaf classification, per-node self_pct/self_work computation —
  using `examples/search_ranking/customer_data`.
- `OpenSourceAPICatalog`: hit / miss / version match / LLM-fallback cache.
- `ServiceSkeletonGen` + `BehaviorGen`: render snapshot assertions — generated
  `.h`/`.cpp` contain the correct nested calls, real open-source calls, and
  `noinline` attributes.
- `StructuralComparator`: synthetic customer/workload flamegraphs verifying
  overlap computation, custom-frame exemption, depth/parent matching.
- `Pipeline.run_and_compare`: end-to-end on ARM with `examples/search_ranking`
  (integration test, `@pytest.mark.skip` when devkit/perf unavailable).

Quality gates unchanged: ruff + mypy (strict) + pytest; pre-commit.

---

## 10. File Layout (NEW / MODIFY)

```text
src/codegen/
  call_tree.py                        (NEW) CallTreeBuilder + IR models
  catalog.py                          (NEW) OpenSourceAPICatalog
  skeleton_gen.py                     (NEW) ServiceSkeletonGen (Layer1/2)
  behavior_gen.py                     (MOD) leaves: real calls + synthesis; compute_type fix
  generator.py                        (MOD) wire CallTreeBuilder→skeleton→behavior→knob
  templates/service/                  (NEW) service.h.j2, service.cpp.j2, stage.h.j2, main.cpp.j2 (request-driven)
  templates/behaviors/                (MOD) compute/memory/mixed.j2 archetype kernels; direct_call uses catalog
src/config/
  open_source_api_catalog.yaml         (NEW)
src/profile/
  structural_comparator.py             (NEW)
src/ingestion/
  flamegraph_parser.py                (MOD) +parse_stacks() public
src/harness/
  metrics_collector.py                 (MOD) +collect_flamegraph()
  pipeline.py                         (MOD) +run_and_compare(); extend run_full_pipeline
src/agent/prompts/
  detail_fill.md                      (MOD) leaf-details only
tests/codegen/ test_call_tree.py, test_catalog.py, test_skeleton_gen.py  (NEW)
tests/profile/ test_structural_comparator.py                            (NEW)
docs/superpowers/specs/ 2026-08-07-callstack-alignment-design.md        (NEW, this file)
```

---

## 11. Relation to Later Fronts

- **Front B (Topdown-driven synthesis):** builds on this spec's per-node
  self-work and archetypes by adding a Topdown L1/L2 → behavior-parameter
  search that tunes `units`/`archetype`/`working_set` to converge Topdown
  targets. The `StructuralComparator` + ARM collect path delivered here is the
  measurement substrate Front B needs.
- **Front C (auto-iteration):** wires the Agent's `evaluate_comparison` +
  priority strategy (`decide_iteration_priority`) into a
  `compare→adjust→regen` loop over the `SkeletonDescriptor` + `config.json`
  (structural changes via descriptor; param changes via config — no regen).
