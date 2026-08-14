# RFC 0001: Thread-pool-aware modular codegen

- **Status:** Draft
- **Number:** 0001
- **Author:** JackWeiw
- **Date:** 2026-08-14
- **Related issues/PRs:** #39 (P1 module-graph IR + modular codegen)

## Summary

Make the modular codegen path (`generate_from_module_graph`) thread-pool-aware
so a generated workload emulates *how* the customer's functions are distributed
across thread pools — not just *what* they compute. P1 shipped the IR seam
(`FunctionSignature.thread_pool`, always `None`) and single-threaded emission;
this RFC fills the seam: recover pool membership, emit per-pool setup/teardown
in `module.cpp`, and preserve per-function `self_pct` across the emulated
threads.

## Motivation

- **Problem:** Today every module emits sequential functions called from a flat
  `main`. Real customer services run hot paths on dedicated pools (a compute
  pool, an IO pool, a scheduling pool). Sequential emission distorts the
  microarchitectural profile — contention, cache sharing, and scheduler
  behavior all differ. The synthetic flamegraph will not mirror the customer's
  Topdown/memory shape under concurrency.
- **Who hits it / when:** Anyone using mirage's automated iteration loop to
  converge a synthetic workload onto a customer profile that has >1 dominant
  thread. P1 is fine for single-threaded or pool-agnostic targets; P2 is
  needed the moment a customer profile is multi-threaded.
- **Why now:** P1 deliberately left `thread_pool` as a passthrough field so
  this work is non-breaking and additive. The IR already carries the seam;
  leaving it unfilled means the field is misleading dead weight.
- **Observations motivating it:** the P1 spec (`docs/superpowers/specs/
  2026-08-14-module-graph-ir-design.md` §3.5) reserves `thread_pool` for P2;
  PR #39 shipped it as `None`.

## Proposed design

### Shape of the change

- **Recovery (`module_graph_builder.py`):** add pool inference. A function's
  pool is derived from the customer stack — either an explicit thread-name
  annotation in `Profile` (if perf captured thread context) or a deterministic
  heuristic (e.g. a function's caller-pool is inherited; functions in the same
  module default to the same pool unless a boundary frame says otherwise).
  Populate `FunctionSignature.thread_pool` with a stable pool id (e.g.
  `"compute"`, `"io"`), still `None` when undetermined.
- **New IR (minimal):** a `ModuleGraph.pools` section — `list[ThreadPool]`
  where `ThreadPool = {name, size, affinity}`. `size` comes from the customer
  profile's observed thread count; `affinity` is a knob (core mask) for P2
  defaults, P3 for fidelity.
- **Codegen (`generator.py` + `module.cpp.j2`):** Phase 2 (impls) emits, per
  module, the function bodies unchanged *plus* a pool entry point that spawns
  `pool.size` worker threads, dispatches the pool's functions round-robin or
  by the recovered call shape, and joins. The flat `main` (from the scaffold)
  calls each module's pool entry point instead of bare functions.
- **Knobs:** `config.json` gains `pools: {<name>: {size, affinity}}`,
  overridable; defaults match the recovered customer values so an unmodified
  run reproduces the observed concurrency.

### How it fits the existing architecture

- Reuses P1's two-phase emit (contracts → impls) and topological order; pools
  are a Phase-1.5 concern added to module headers as `// pool: <name>`.
- Reuses `SelfWork.units` for per-function work — the loop body is unchanged;
  only the *dispatch wrapper* is new.
- `generate_from_module_graph` stays the entry point; no change to the legacy
  `generate()` / `SkeletonDescriptor` path.

### Key invariants

- **Self%-preservation:** a function's `units` is its self-time budget. Split
  across `pool.size` threads, total self-time must still match the customer
  `self_pct`. The dispatch divides work, not the per-iteration cost.
- **Acyclic modules:** pool membership does not create new `depends_on` edges;
  the DAG invariant from P1 holds.

### Non-goals

- No per-module sub-agent fan-out (P3).
- No LLM-based pool discovery.
- No NUMA/affinity fidelity beyond a static core mask (P3).
- No dynamic thread spawning at runtime — pools are fixed-size for
  measurement stability.

## Alternatives considered

- **Single pool for everything:** simplest, but collapses the customer's
  concurrency structure into one thread group — defeats the purpose. Rejected.
- **Per-function thread (no pools):** over-parallelizes; real services bound
  concurrency by pool. Distorts contention. Rejected.
- **Thread pools as a separate IR layer above ModuleGraph:** cleaner layering
  but doubles the recovery surface; coupling pools to functions (where the
  self-time lives) is more faithful. Revisit if pool state grows beyond
  size+affinity.

## Risks / trade-offs

- **Recovery fidelity:** perf flamegraphs don't always record which thread a
  stack ran on. Mitigation: fall back to `thread_pool=None` (single-threaded
  emission) and log it — never fabricate a pool.
- **Measurement noise:** thread scheduling jitter widens the synthetic
  profile's variance. Mitigation: fixed-size pools, warmup, and reporting
  median-over-runs (already in the knob layer).
- **Complexity:** pool entry-point codegen adds template surface. Mitigation:
  one `pool.cpp.j2` template, reused per pool.

## Rollout / migration

- Additive: `thread_pool` defaults to `None` → P1 behavior. Existing P1 tests
  unchanged. New tests cover pool recovery + emission.
- Behind the same `generate_from_module_graph` entry point; no flag needed —
  a graph with `pools == []` behaves exactly as P1.

## Open questions

- How is pool membership recovered when the customer `Profile` has no thread
  context — pure heuristic, or do we require enriched perf input first?
- Should pool `size` be a knob (mutable at runtime) or baked into the binary?
- Do we model pool work queues (FIFO/LIFO) or is round-robin enough for P2
  fidelity?

## Links

- PR #39 (P1 implementation)
- Spec: `docs/superpowers/specs/2026-08-14-module-graph-ir-design.md` §3.5
