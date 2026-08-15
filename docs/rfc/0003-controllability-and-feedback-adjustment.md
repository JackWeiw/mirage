# RFC 0003: Controllability & feedback-adjustment contract for automated iteration

- **Status:** Draft
- **Number:** 0003
- **Author:** Jack Wei
- **Date:** 2026-08-15
- **Related issues/PRs:** builds on RFC 0001 & 0002; refills the slot removed in #32; unblocks Phase 2 auto-iteration.

## Summary

Phase 2 auto-iteration cannot converge without (a) a steerable plant and (b) a
feedback→adjustment leg. Today the agent is one-shot (`run_full_chain`), the
iteration-evaluation step was deleted as dead code (#32), and
`decide_iteration_priority` only ranks which metric to attack — it emits no
adjustment. This RFC proposes: a named, codegen-honored knob space; a structured
adjustment schema the agent emits and codegen applies; an agent
`revise_instruction` step that refills the deleted slot; and a mandatory
steerability spike as the empirical gate before the loop is wired.

## Motivation

- The Phase 2 goal — converge to Topdown <10%, memory bandwidth <5%, hotspot
  coverage >80% — is a closed-loop control problem. A control loop needs a plant
  that responds to inputs and a controller that reads the error and emits a
  corrective input. Neither leg is implemented today.
- `agent_core.run_full_chain` is one-shot analyze→plan→fill. There is no method
  that takes a comparison report + prior instruction and emits a revised
  instruction. `evaluate_comparison` was removed in #32 as dead code — the slot
  exists conceptually but was never filled for real.
- `decide_iteration_priority` ranks which metric to attack first; it produces no
  knob deltas. Ranking is not steering.
- **Unverified assumption:** the synthesis knobs (`working_set_mb`,
  `access_pattern`, `archetype`, `compute_ratio`, `memory_ratio`) actually move
  Topdown L1 and memory bandwidth in a readable, monotonic direction. If they
  don't, no agent — however smart — can converge, because there is no lever.
  This must be proven before the loop is built, not after it fails to converge.
- **Why now:** RFC 0001 (thread-pool codegen) and RFC 0002 (context compaction)
  are P2 enablers that both *presuppose* a control contract exists — 0002
  compacts iteration context that is not yet defined; 0001 enhances generation
  that the loop steers. The contract this RFC defines is the prerequisite for
  both, so it must land first.

## Proposed design

### 1. Named knob space (the steering contract)

Codify the set of knobs the agent may steer, drawn directly from the codegen
templates (verified against `behaviors/*.cpp.j2` and `config/config.json.j2`):

- **Structural** (require regenerate — mutate `synthesis_config`):
  - `memory_synthesis`: `working_set_mb`, `access_pattern`
    (`sequential`|`random`|`mixed`), `iterations`
  - `compute_synthesis`: `archetype` (`hash`|`matmul`|`sort`|`branch`|`compute`),
    `iterations`
- **Runtime** (mutate `config.json`, no rebuild): `compute_ratio`,
  `memory_ratio`, `thread_count`, `qps`

Each knob carries an *expected* metric direction and the metric it targets,
recorded in a sensitivity table filled empirically by the spike (§4). Example
hypothesis: `working_set_mb ↑ (above cache) → backend_bound ↑`. The hypotheses
are exactly what the spike confirms or refutes.

### 2. Adjustment schema

A structured delta the agent emits per iteration:

```json
{"stage": "mem_stage", "knob": "working_set_mb", "from": 64, "to": 256,
 "rationale": "backend_bound 12% below target; widen working set",
 "expected_metric": "backend_bound", "expected_direction": "up"}
```

Codegen honors it by mutating the named `synthesis_config` field (structural) or
`config.json` (runtime) and regenerating. This replaces free-text instruction
rewriting with a named, reproducible, inspectable contract — a non-converging run
can be debugged knob-by-knob instead of by re-reading LLM prose.

### 3. Agent revise step

```
revise_instruction(prior_instruction, comparison_report, sensitivity_table, history)
    -> (revised_instruction, list[adjustment])
```

Fills the slot vacated by #32. It reads the comparator's per-metric diffs + the
sensitivity table to pick knobs whose expected direction reduces the largest
error, emits adjustments, and applies them to produce the revised instruction.
`history` (prior adjustments + their observed effects) is consulted to avoid
oscillation (don't toggle a knob back and forth across iterations).

### 4. Steerability spike — mandatory gate

Before wiring the auto-iteration loop, run `examples/steerability_spike.py`
(companion to this RFC). It sweeps the named knobs one-at-a-time (OAT,
low/mid/high) on ARM, builds + runs + collects per point, and records absolute
Topdown L1 + memory bandwidth per knob value.

- **Pass gate:** each key microarch knob (`working_set_mb`, `access_pattern`,
  `archetype`, `compute_ratio`, `memory_ratio`) shows monotonic movement in the
  expected direction on at least one target metric.
- **Fail gate:** a dead knob becomes a codegen ticket (expose/fix the knob)
  before the loop is wired. The loop is not built against an unverified plant.

Output: `sensitivity.json` (raw rows) + `sensitivity.md` (per-knob table with
direction + monotonicity verdict: `controllable` / `weak` / `dead`).

## Alternatives considered

- **Skip the spike, wire the loop directly.** Rejected: blind search over an
  unverified plant cannot converge and burns LLM budget on a system that may
  have no lever. Revisit only if the knob space is proven elsewhere.
- **Agent revises instruction in free text each turn (no schema).** Rejected: no
  steerable contract, non-reproducible, no way to learn knob→metric direction,
  impossible to debug a non-converging run. The schema's whole value is making
  steering explicit.
- **Sweep runtime config only (no regenerate).** Cheapest, but answers only
  "does the binary respond at all," not whether the structural knobs the agent
  must steer for Topdown/coverage are controllable. Kept as a documented
  fallback if ARM rebuild cost proves prohibitive.

## Risks / trade-offs

- **Knobs may be dead** (no metric movement, e.g. `compute_ratio` if `main.cpp`
  never reads it). Mitigation: the spike is the gate; a dead knob is surfaced as
  a verdict and becomes a codegen ticket before the loop.
- **Sensitivity may be non-monotonic/noisy** (cache cliffs, thread contention).
  Mitigation: OAT 3-point; repeat-if-noisy is a documented spike option.
- **Knob space may be too small** to hit all three targets simultaneously. Open
  question; the spike's per-knob verdict surfaces this and may demand new knobs
  (LLC pressure, branch patterns) — which becomes a codegen follow-up, not a loop
  change.
- **Adjustment schema adds a layer** between agent and codegen. Accepted: the
  indirection is the point — it makes steering explicit and testable.

## Rollout / migration

- **No existing caller breaks:** the spike is a new entry point;
  `revise_instruction` is additive; the adjustment schema is a new optional
  path. `run_full_chain` stays as-is for Phase 1 single-pass.
- **The loop driver** (wiring `revise_instruction` into `run_full_pipeline`)
  is explicitly out of scope here and gated on the spike passing; it lands in a
  follow-up PR.
- **Tests:** the spike is disposable (no unit tests — it is a measurement
  harness, not production code); `revise_instruction` and schema application get
  unit tests when implemented in the follow-up.

## Open questions

- Does `main.cpp` actually consume `compute_ratio`/`memory_ratio` to mix calls,
  or are they inert? The spike answers this; if inert, they are removed from the
  knob space or wired in.
- Is the devkit topdown collection workload-attributable, or only system-wide?
  Affects spike collection orchestration (system-wide is acceptable on a quiet
  box; see spike docstring).
- What new knobs are needed if the current set cannot reach all three targets?

## Links

- RFC 0001 (thread-pool-aware modular codegen) — depends on this contract.
- RFC 0002 (context compaction for iteration) — compacts the iteration context
  this RFC defines.
- #32 (removed dead `evaluate_comparison`) — the slot this RFC refills.
