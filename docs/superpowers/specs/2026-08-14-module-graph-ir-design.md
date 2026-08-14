# Module-Graph IR, Recovery & Modular Codegen (P1, single-threaded)

> Date: 2026-08-14
> Status: Draft
> Parent spec: [2026-07-27-workload-simulation-design.md](./2026-07-27-workload-simulation-design.md)
> Related issues: #26, #28, #36
> Scope: P1 of a three-part effort (P1 module-graph / P2 multi-thread-pool /
> P3 per-module sub-agent fan-out). This spec covers **P1 only**: a new
> `ModuleGraph` IR, its **deterministic recovery** from the customer
> flamegraph, and **modular codegen** that emits multi-compilation-unit C++
> with real cross-module calls. Single-threaded. No LLM in the loop.

## 1. Problem Statement

### Context

The customer programs mirage targets are **modular C++ applications**: their
flamegraphs encode not just a flat list of hotspots but a **module structure**
with inter-module interfaces and dependencies. mirage's current ingestion
flattens this structure into a single service call tree (`SkeletonDescriptor`:
one service function calling a sequence of **independent** stages, each one
`.h`, no inter-stage calls, no interface contracts). The modularity of the
customer program — which namespaces group into modules, which functions are a
module's public API, which modules depend on which — is **latent in the
flamegraph** and currently thrown away.

A faithful "mirror" of the customer program must reproduce that modularity:
multiple compilation units, public interface headers, and cross-module calls
that respect real dependency edges. This is also the **prerequisite** for P3
(per-module sub-agent fan-out): you cannot dispatch "implement module B against
module A's interface" without a materialized interface contract.

### What goes wrong today

- `HotspotFunction.function` stores the raw demangled frame string (which often
  **contains the real signature**: namespace + name + parameter types), but the
  pipeline never uses the namespace/signature structure to recover modules.
- `SkeletonDescriptor` is a single call tree; there is no module node kind and
  no interface-contract model.
- `detail_fill` emits a raw `dict` (no pydantic model); stages are a flat list.
- codegen is fully serial, single-threaded, single compilation unit per stage.

## 2. Goals & Non-Goals

### Goals (P1)

- Define a `ModuleGraph` IR (modules / public interface / internal functions /
  dependency edges) that captures the customer program's modular structure.
- **Deterministically recover** a `ModuleGraph` from a `Profile` + classifier —
  namespace clustering, call-path edge classification, real-signature reuse. No
  LLM. Reproducible and unit-testable.
- Emit **modular C++**: one `module.h` (public declarations) + one `module.cpp`
  (implementation) per module, cross-module calls through public headers, all
  compilation units listed in `CMakeLists.txt`. Single-threaded.
- Preserve microarchitectural fidelity: call frequency / self% are retained as
  node/edge attributes so the generated workload still mirrors the customer's
  Topdown/memory/hotspot profile.

### Non-Goals (explicitly deferred)

- **P2 — multi-thread-pool model**: recovering which modules run on which
  thread pool, concurrency levels, cross-pool synchronization. P1 leaves a
  passthrough `thread_pool` field in the IR (always empty in P1) so P2 does not
  reshape the IR. Note P2 will likely also require ingestion changes (the
  current `FlamegraphParser` merges stacks across threads).
- **P3 — per-module sub-agent fan-out**: dispatching N sub-agents to implement
  modules in parallel against fixed contracts. P1 generates each module in a
  single deterministic pass but leaves the "contracts before impls" seam clean.
- **LLM architecture agent**: business-semantic module labeling and ambiguous
  grouping refinement. Deferred to P3 (deterministic-only in P1, per the repo
  ethos of "inject deterministic classification, drop LLM re-classification").
- **Context compaction** for the iteration loop — separate Phase-2 concern.
- Touching the legacy agent-dict `generate()` path or the `SkeletonDescriptor`
  single-service path — both stay as-is for non-modular scenarios.

## 3. Design

### 3.1 The IR — `ModuleGraph`

A module is a **namespace-level** grouping (consistent with the existing
classifier's namespace rules in `open_source_libraries.yaml`). A function is
**public** iff it is called by a frame **outside its namespace**; otherwise it
is **internal** (private to the module).

`CallSpec` (`{includes, statement, setup}`) is reused as the **calling-side**
carrier (it fully covers cross-module call info: `#include` + call statement +
setup). The **declaration side** is not carried by `CallSpec`; it is handled by
reusing the real customer signature (see 3.2) plus a `declaration` field on the
signature wrapper, materialized in the IR so contracts are stable and
inspectable without executing strategy code (required by P3).

```python
class FunctionSignature(BaseModel):
    function: str
    namespace: str
    call_spec: CallSpec              # reused — calling-side info
    declaration: str | None          # materialized prototype, e.g. "void lookup(int)"
    self_work: SelfWork              # reused — microarchitectural work to mirror
    thread_pool: str | None = None   # passthrough for P2; always None in P1


class ModuleDescriptor(BaseModel):
    name: str                        # business name, e.g. "index"
    namespace: str                   # e.g. "foo::index"
    public_interface: list[FunctionSignature]   # cross-namespace-called functions
    internal_functions: list[FunctionSignature] # called only within the namespace
    depends_on: list[str]            # module names this module calls (directed edges)


class ModuleGraph(BaseModel):
    project_name: str
    modules: list[ModuleDescriptor]
    config: dict[str, Any] = Field(default_factory=dict)
```

`depends_on` edges are derived directly from flamegraph call paths: a
parent→child edge is **inter-module** when parent and child live in different
namespaces (→ a `depends_on` edge; the child is marked public), and
**intra-module** when they share a namespace. Call frequency / self% are
preserved as attributes on nodes/edges.

### 3.2 Signature reuse — real, not invented

mirage synthesizes **stub function bodies** (the body is a behavior-mirroring
kernel, not the customer's proprietary algorithm — mirage mirrors
microarchitectural behavior, not source logic, which we do not have). But the
**declaration** should reuse the customer's real signature whenever the
flamegraph provides it, rather than inventing one:

| Function source | Declaration |
|---|---|
| Real demangled frame symbol (open-source leaves, named customer functions) | Parse namespace + name + parameter types from the frame string; return type defaults to `void` (C++ mangling does not encode return type, and mirage's synthesized call statements do not consume return values — we mirror call-path shape + self-time, not data flow). |
| Collapsed `custom_synth` subtree (a customer-private sub-tree deliberately collapsed into one synthetic kernel — no single real symbol represents it) | Designed name (already the case today, e.g. `<parent>_synth`); inherently synthetic, nothing to reuse. |

Signature parsing of demangled symbols is **deterministic** (fixed grammar) and
consumes no LLM budget.

### 3.3 Recovery pipeline — deterministic only (P1)

Principle: use determinism wherever possible (repo ethos, #30). The pipeline
has one layer in P1; the LLM refinement layer is deferred to P3.

1. **Parse frame strings** → namespace + real signature (name + params; return
   type `void` default). Reuse the classifier's namespace rules.
2. **Cluster by namespace** → candidate modules (namespace-level).
3. **Classify call-path edges**: parent→child same namespace = intra-module;
   cross-namespace = inter-module. Inter-module edges populate `depends_on` and
   mark the child function **public**; functions only called within their
   namespace are **internal**.
4. **Preserve call frequency / self%** as node/edge attributes (microarchitectural
   fidelity).
5. **Cycle detection**: `ModuleGraph` must be a DAG (a buildable dependency
   graph). Cycles fail loud (a cycle in module dependencies would break
   topological emission) — surface at recovery, do not silently break.

This layer is reproducible and unit-testable from synthetic flamegraphs with no
LLM.

### 3.4 Modular codegen emission

**Build order (topological):** sort modules by `depends_on`. Two-phase
emission — **contracts before impls** — which is exactly the seam P3 will
exploit (contracts pinned first, impls then fan out).

**Phase 1 — contracts (`module.h`, deterministic, no strategy):**
- One `module.h` per module containing only **public-function declarations**
  using the real materialized signatures.
- `direct_call` / open-source functions: `module.h` `#include`s the real library
  header; no re-declaration.
- Pure-synthetic (`custom_synth`) public functions: declare the prototype using
  the designed signature.

**Phase 2 — impls (`module.cpp`, strategy-driven):**
- One `module.cpp` per module. Synthetic-function bodies come from the
  strategy's `render_def()`; `direct_call` functions call the real library
  function.
- Cross-module calls go through `#include "other_module.h"` + the call statement
  (`CallSpec`, reused).
- `service.cpp` / `main.cpp` wire modules in the **customer call-path order**,
  preserving self%/counts.
- `CMakeLists.txt` lists every module `.cpp` as a separate compilation unit.

**Strategy interface change (the main code surface of P1):** the current
`BehaviorStrategy.render(stage, env)` returns one `.h` with decl+def fused. It
is split into:
- `render_decl(stage, env) -> str` → into `module.h`
- `render_def(stage, env) -> str` → into `module.cpp`
- `render()` retained as a compatibility shim that calls both and concatenates,
  deprecated over time.

**Coexistence (no legacy regression):**
- P1 lives on the **pydantic descriptor path** (`generate_from_descriptor`).
  A new `ModuleGraphBuilder` (analogous to `CallTreeBuilder`) consumes a
  `Profile` + classifier and produces a `ModuleGraph`. A new
  `WorkloadGenerator.generate_from_module_graph(desc)` emits the modular
  workload.
- The legacy agent-dict `generate()` path is **not touched**.
- `SkeletonDescriptor` (single-service call tree) is **unchanged** and retained
  for non-modular scenarios.

### 3.5 Single-threaded scope + thread-pool passthrough

P1 codegen is single-threaded (sequential / request-driven `main`, as today).
The IR carries a passthrough `thread_pool: str | None` field (always `None` in
P1; codegen does not read it) so P2 can fill it without reshaping the IR. P2 is
expected to require ingestion changes to capture thread-pool membership (the
current `FlamegraphParser` merges stacks across threads) — that is P2's work.

## 4. Alternatives Considered

- **LLM-dominant recovery** (architecture agent infers modules from business
  semantics, deterministic pass only validates). Rejected for P1: not
  reproducible, harder to test, and against the "deterministic where possible"
  ethos. LLM refinement is kept as a *deferred* P3 layer on top of the
  deterministic skeleton.
- **Coarser "module = class" granularity.** Rejected: class boundaries are less
  stable in flamegraphs than namespaces, and would yield too many modules.
  Namespace-level matches the existing classifier.
- **Extend `SkeletonDescriptor` with a `module_graph` field** instead of a new
  descriptor. Rejected: `SkeletonDescriptor` models a single call tree
  (single-service, sequential stages); bolting a module graph onto it conflates
  two shapes. A separate `ModuleGraph` + builder keeps both paths clean.
- **Reusing `CallSpec` to also carry declarations** (add a `declaration` field
  to `CallSpec`). Rejected: `CallSpec` is the open-source API catalog model
  (calling-side only). Declarations live on `FunctionSignature` (the
  module-level wrapper) to avoid polluting the catalog model.

## 5. Risks / Trade-offs

- **Demangled-symbol parsing is format-dependent.** `perf` demangling is mostly
  stable but not all frames are demangled (some show mangled or truncated
  names). Mitigation: fall back to the raw frame string as the function name
  with empty params when parsing fails; fail loud only on cycles, not on
  unparseable signatures (a missing param list is recoverable, a dependency
  cycle is not).
- **`void` return-type default loses fidelity.** mirage's synthesized call
  statements do not consume return values, so `void` is safe for behavior
  mirroring; it is a fidelity gap only if a future need arises to mirror
  data-flow. Accepted for P1.
- **Strategy `render` split is a cross-cutting refactor** touching all four
  strategies and their templates. Mitigation: keep `render()` as a shim calling
  `render_decl`+`render_def`; add a regression test asserting the concatenated
  output matches today's `render()` for each strategy.
- **Single-threaded assumption hides concurrency structure.** A
  single-threaded emission of an inherently multi-thread-pool customer program
  will diverge in Topdown/memory until P2 lands. Accepted — P1 is foundational,
  not final.

## 6. Rollout / Migration

- New code only: `ModuleGraph` model, `ModuleGraphBuilder`,
  `WorkloadGenerator.generate_from_module_graph`, strategy
  `render_decl`/`render_def`.
- No change to existing call paths (`generate_from_descriptor`, agent-dict
  `generate`) — opt-in via the new entry point. No flags needed; the modular
  path is selected by calling the new builder + generator directly.
- Strategy split is backward-compatible (shim). No existing caller breaks.
- Landing order within P1: IR + builder + tests → strategy split + regression
  tests → modular generator + codegen tests. Each layer lands with tests before
  the next.

## 7. Testing

- **Recovery** (deterministic, no LLM): synthetic flamegraphs with hand-crafted
  cross-namespace call paths → assert module partitioning, public/internal
  classification, `depends_on` edges, signature parsing, and cycle fail-loud.
- **IR**: pydantic round-trip; topological sort; cycle detection raises.
- **Codegen**: a 3-module graph → assert one `module.h` + one `module.cpp` per
  module, correct cross-module `#include`s, all compilation units in
  `CMakeLists.txt`, and `cmake` configure succeeds (full build not required,
  matching existing test style).
- **Strategy split**: for each strategy, `render_decl()` + `render_def()`
  concatenated equals today's `render()` output (regression protection).

## 8. Relationship to P2 / P3

- **P2 (multi-thread-pool)**: consumes the `thread_pool` passthrough field; will
  likely extend ingestion to capture thread-pool membership and add a threading
  model layer to codegen. Does not reshape the P1 IR.
- **P3 (per-module fan-out)**: the architecture agent (LLM refinement layer,
  deferred from 3.3) runs on the deterministic `ModuleGraph`, then dispatches
  one sub-agent per module to produce `module.cpp` against the already-emitted
  `module.h` contracts. The "contracts before impls" two-phase emission (3.4) is
  the seam P3 parallelizes. Generalizes #26 (per-stage `detail_fill` fan-out)
  and #28 (declarative sub-task DAG) to module granularity.

## 9. Open Questions

- Should `ModuleGraphBuilder` consume the same `Profile` that `CallTreeBuilder`
  does, or a richer profile shape that retains per-thread stack origin (needed
  by P2)? P1 can consume the current `Profile`; P2 may widen it. To be settled
  when P2 is scoped.
- Exact CMake target shape (one static lib per module vs. one executable with N
  sources) — decide during implementation; default is one executable with all
  module `.cpp` as sources, matching today's scaffold.
