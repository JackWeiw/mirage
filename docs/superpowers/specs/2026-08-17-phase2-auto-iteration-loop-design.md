# Design — Phase 2 auto-iteration loop

- **Date:** 2026-08-17
- **Author:** Jack Wei
- **Related:** RFC 0003 (merged `4e13dff`), PR #49 (ratio wiring), the §4 spike
  (gate PASSED 2026-08-17), follow-up #47 (DevkitConfig)
- **Status:** Approved (controller model confirmed by user 2026-08-17)

## Context

RFC 0003 landed the controllability contract (named knob space, adjustment
schema, the `revise_instruction` *signature*) and its §4 spike gate **passed**
on 2026-08-17 — all five key microarch knobs are `controllable`. The Phase 2
auto-iteration loop is therefore unblocked. This spec designs the loop driver
and the `revise_instruction` implementation that RFC 0003 deliberately left out
of scope ("lands in a follow-up PR").

Existing pieces to wire (verified by reading the code):
- `AgentCore` (`src/agent/agent_core.py`): LLM infra — `_call_llm_json` with
  retries/truncation/JSON-parse, `run_full_chain` (analyze→plan→detail_fill).
  **No `revise_instruction` yet** (the slot #32 vacated).
- `Pipeline` (`src/harness/pipeline.py`): one-shot `run_full_pipeline`
  (ingest→generate→build→return). Has `run_and_compare` and `compare_results`
  as building blocks. `MetricsCollector` is constructed with no args.
- `decide_iteration_priority` (`src/agent/strategy.py`): returns 0–4 priority —
  0 converged, **1 config-only (runtime)**, 2 behavior profiles, 3 workflow,
  4 skeleton. This escalation is reused as the tier selector.
- `ProfileComparator` report shape (from `compare_results`):
  `report["topdown_l1"][metric]["diff_pct"]` + `["within_threshold"]`,
  `report["convergence"]["converged"]`/`["reason"]`,
  `report["hotspot_coverage"]["coverage_pct"]`,
  `report["memory"]["bandwidth_gbps"]["diff_pct"]`.
- `MetricsCollector.collect_topdown(output_path, duration, interval, pid)`
  (PR #45, fixed): runs `devkit tuner top-down -d .. -i .. [-p ..]`, writes the
  TEXT report. **Has no config source for duration/interval/pid** (#47).
- `FrameworkConfig`: `agent`, `comparison` (thresholds 10/5/80), `run_defaults`,
  `codegen`, `harness`. **No devkit field** — #47 adds `DevkitConfig`.
- `IterationHistory`/`IterationRecord`: tracks per-iter topdown_diffs, memory,
  coverage, priority, best_iteration, `is_converging()`. **No adjustments or
  per-knob observed effect** — extended here for oscillation avoidance.
- Spike `sensitivity.json`: per-knob `{target_metric, expected, metric_values,
  verdict}` — the sensitivity table the controller consults.

## Controller model — LLM-primary realism + deterministic-assist

A purely deterministic controller was **rejected**: it would emit overly regular
code patterns that a chip-level optimizer (compiler, HW prefetcher/branch
predictor, or a profiling-driven optimizer) could pattern-match and over-fit to
a single hotspot — defeating mirage's purpose, which is to **replicate the
customer's real business-code logic and content**, not emit a synthetic that a
silicon optimizer can see through. So:

- **LLM is primary** (the realism engine). It owns **structural / business-logic
  revision**: stage structure, call patterns, archetype, access pattern, the
  shape of the generated code. This is what makes the workload look like real
  customer code and what resists over-fitting. Requires regenerate + rebuild.
- **Deterministic controller is the assist.** It owns **runtime-config knob
  tuning** only — `compute_ratio`, `memory_ratio`, `thread_count`, `qps`. These
  live in `config.json`, **not in the code**, so tuning them does not change the
  code's pattern or realism at all; and the spike proved them to be pure
  direction-lookups (controllable, monotonic). So this leg is mechanical,
  reproducible, unit-testable, costs no LLM budget, and needs **no rebuild**.

This split maps 1:1 onto `decide_iteration_priority`: **priority 1 (config-only)
→ deterministic runtime fast path; priority ≥ 2 (behavior/workflow/skeleton) →
LLM structural revision**.

## Two-tier loop driver

New `Pipeline.run_iteration_loop(...)`; `run_full_pipeline` stays for Phase-1
single-pass. The two tiers differ in *whether they regenerate/rebuild*:

- **Structural tier (LLM, priority ≥ 2):** the instruction's *code* changed →
  `project = generate(instruction); binary = build(project)`. Expensive.
- **Runtime tier (deterministic, priority == 1):** only `config` changed → do
  **not** regenerate or rebuild. Rewrite `project/config.json` from the mutated
  `instruction["config"]` and re-run the **existing** `binary` (which reads
  `config.json` at runtime). Cheap — this is the fast path.

Pseudocode (binary is carried across iterations; regenerated only on the
structural tier):

```
customer_profile = ingest_customer_data(...)
instruction     = agent.run_full_chain(profile_json)      # Phase-1 LLM: realistic initial instruction
history         = IterationHistory(customer_name)
project = generate(instruction); binary = build(project)  # initial structural build
if binary is None: return fail(history)
for i in range(max_iter):
    workload_prof = run_and_collect(binary, instruction["config"])  # taskset + warmup + devkit + parse
    report        = compare(customer_profile, workload_prof)
    history.add(IterationRecord(i, report, adjustments_this_iter))
    if report.convergence.converged: break
    priority = decide_iteration_priority(report, config.comparison)
    if priority == 1:                                       # RUNTIME tier (no rebuild)
        adjustments = deterministic_revise(report, sensitivity, history)
        apply_adjustments(instruction, adjustments)         # mutate instruction["config"] only
        write_config_json(project/"config.json", instruction["config"])  # binary reused next pass
    else:                                                    # STRUCTURAL tier (LLM + rebuild)
        instruction, adjustments = agent.revise_instruction(instruction, report, sensitivity, history)
        apply_adjustments(instruction, adjustments)         # mutate synthesis_config (structural)
        project = generate(instruction); binary = build(project)   # regenerate + rebuild
        if binary is None: break
    if is_oscillating(history) or no_improvement_for(history, K): break
return PipelineResult(..., best_iteration=history.best_iteration, history=history)
```

`run_and_collect` mirrors the spike's proven orchestration (`run_one_point` in
`examples/steerability_spike.py`): taskset-pin the binary, warm up, collect
topdown for `measurement_seconds` with `-p <pid>`, wait for exit. The spike is
the reference implementation for this leg.

### Prerequisite for the runtime no-rebuild fast path (must be resolved in PR 3)

The runtime tier reuses the binary and rewrites `config.json` — this only works
if the binary **reads `config.json` at runtime**. The production
`config_loader.h.j2` parses `config.json` via `#include <nlohmann/json.hpp>`,
but the spike proved **nlohmann is not available on the bare ARM target**
(that is precisely why the spike bakes config values into `config_loader.h`).
So the no-rebuild runtime tier has an unsolved build dependency on ARM. PR 3
must resolve this — either (a) fetch nlohmann via CMake `FetchContent` so the
production build works on ARM, or (b) make `config_loader.h.j2`
nlohmann-free with a minimal hand-rolled JSON reader (a few dozen lines for the
flat `RunConfig` schema). Option (b) is preferred (no external fetch on a bare
toolchain, matches the spike's independence). Without this, the runtime tier
degenerates to regenerate+bake+rebuild per iteration (still correct, just not
"fast") — acceptable as a fallback but not the approved design.

## Components to add

### 1. Adjustment schema + `apply_adjustments` (deterministic, no LLM)
Schema (RFC §2, unchanged):
```json
{"stage": "mem_stage", "knob": "working_set_mb", "from": 64, "to": 256,
 "rationale": "...", "expected_metric": "backend_bound", "expected_direction": "up"}
```
New `apply_adjustments(instruction, adjustments) -> instruction` (pure function):
routes each adjustment by `(stage, knob)` — structural knobs (`working_set_mb`,
`access_pattern`, `iterations`, `archetype`) mutate
`stages[i].strategies[0].synthesis_config[knob]` (matched by `stage_name`);
runtime knobs (`compute_ratio`, `memory_ratio`, `thread_count`, `qps`) mutate
`config[knob]`. Validates `knob` is in the named knob space (RFC 0003 §1) and
that `to` is within the knob's valid domain (enum for `access_pattern`/`archetype`,
positive int/float bounds for the numerics). Raises on unknown stage/knob so a
bad adjustment surfaces loudly instead of silently no-oping. A sibling
`apply_adjustments_to_config(config.json path, adjustments)` writes only the
runtime subset (the no-rebuild fast path).

Location: new `src/agent/adjustment.py` (keeps `strategy.py` for
`decide_iteration_priority`).

### 2. Sensitivity-table loader
`load_sensitivity(path) -> dict[knob, SensitivityEntry]` where
`SensitivityEntry = {target_metric, expected_direction, verdict, values,
metric_values}`. Reads the spike's `sensitivity.json` verdicts. The
deterministic controller consults it; `revise_instruction` is given it as
context (so the LLM does not move a knob against its proven direction).

Location: `src/agent/adjustment.py` (or `src/agent/sensitivity.py`).

### 3. `deterministic_revise` (the assist controller — runtime knobs only)
`deterministic_revise(report, sensitivity, history) -> list[adjustment]`:
1. Find the largest-error metric in `report["topdown_l1"]` (max
   `abs(diff_pct)` not within threshold) and the memory bandwidth error.
2. From the sensitivity table, pick **runtime** knobs whose
   `expected_direction` reduces that error (e.g. `backend_bound` too *high* → a
   knob with `expected_direction: up` on `backend_bound` is *decreased*; one
   whose direction reduces it is *increased*). Constrain `to` to a bounded step
   (e.g. ±0.2 for ratios, ±1 thread, ±20 qps) clamped to `[min, max]`.
3. Skip any knob toggled in the last `window` iterations (history) to avoid
   oscillation; if all candidates are skip-blocked, return `[]` (forces
   escalation to the LLM tier).
Emits adjustments only for runtime knobs. Pure, unit-testable.

### 4. `AgentCore.revise_instruction` (the LLM realism-preserving leg)
Signature (RFC §3): `revise_instruction(prior_instruction, report,
sensitivity, history) -> (revised_instruction, list[adjustment])`.

New prompt `src/agent/prompts/revise_instruction.md` receives:
- the prior instruction (JSON),
- the comparison report's per-metric diffs + convergence reason,
- the sensitivity table (knob → metric → proven direction),
- recent history (prior adjustments + observed effects, to avoid toggling).

The LLM revises the **business logic / structural shape** to better replicate
the customer's real code (diverse, non-regular), constrained by the sensitivity
table (don't move a knob against its proven direction), and emits the
adjustments it applied. `_call_llm_json` + the existing JSON-parse/raise path is
reused. `is_available()` gates this (local-only runs skip it — they can only use
the deterministic tier; the loop driver treats "agent unavailable on a
structural-tier priority" as a stop reason, not a crash).

### 5. `DevkitConfig` plumbing (#47 — wired here, not deferred)
Add to `FrameworkConfig`:
```python
class DevkitConfig(BaseModel):
    devkit_cmd: str | None = None
    duration_seconds: int = 20
    interval_seconds: int = 3
    cpu_range: str | None = None        # taskset pin, e.g. "4"
    collect_pid: bool = True            # -p <pid> attribution (spike-proven)
```
`Pipeline` constructs `MetricsCollector(devkit_cmd=config.devkit.devkit_cmd,
perf_cmd=...)` and `run_and_collect` calls `collect_topdown(path,
duration=config.devkit.duration_seconds, interval=config.devkit.interval_seconds,
pid=proc.pid)`. `duration_seconds` also retires the cosmetic
`BuildRunner.duration_seconds` never-set field (#48) — one source of truth.

### 6. Loop driver + run/collect orchestration
`Pipeline.run_iteration_loop(flamegraph_path, topdown_path, customer_name,
instruction=None, max_iter=10, no_improve_stop=3) -> PipelineResult`. Owns the
two-tier loop above. `run_and_collect(binary, config)` reuses the spike's
taskset-launch → warmup → `collect_topdown(-p pid)` → wait pattern, factored so
the integration test can inject a stub. Closes #47 by reading DevkitConfig.

### 7. History extension
`IterationRecord` gains `adjustments: list[dict]` and
`observed_effects: dict[metric, float]` (the next iteration's diff for each
adjustment's `expected_metric`). `IterationHistory` gains `recent_adjustments(n)`
and `is_oscillating()` (a knob toggled back-and-forth, or
`no_improvement_for(K)`). The deterministic controller and the loop driver both
consult these for stop/escalation.

## Termination
- `report.convergence.converged` (comparator thresholds: topdown < 10%,
  memory < 5%, coverage > 80% from `ComparisonConfig`) → **success**.
- `max_iter` reached → stop, return `history.best_iteration`.
- **Oscillation** (`is_oscillating()`) or **no improvement for `K`** consecutive
  iterations → stop, return best. Do not burn budget on a non-converging run.

## Tests
- `apply_adjustments`: structural + runtime routing by `(stage, knob)`,
  domain validation (enum, bounds), raises on unknown stage/knob, idempotent on
  `from==to`. Pure unit tests, no LLM/devkit.
- `deterministic_revise`: given a fake report + sensitivity table, picks the
  correct runtime knob in the correct direction; respects history
  (skip-blocked knobs force `[]`); clamps to bounds. Pure unit tests.
- `load_sensitivity`: parses the spike's real `sensitivity.json` fixture →
  correct entries.
- `revise_instruction`: mock-agent (recorded JSON response) — asserts the
  revised instruction carries the emitted adjustments and respects a
  sensitivity constraint. No real LLM call.
- **Loop driver integration test**: a **stub plant** — inject a fake
  `collect_topdown` (returns a Profile whose metrics move deterministically
  toward target as knobs adjust) + a mock agent + the real deterministic
  controller. Asserts: converges within N iters, escalates to LLM tier on a
  structural gap, stops on oscillation, returns `best_iteration`. Runs locally
  with no ARM/devkit/LLM — this is the dev/test path confirmed with the user.
- Real-run validation (user-side, on ARM): one `run_iteration_loop` against the
  devkit collector + real LLM, mirroring the spike's box.

## PR structure (relevant-only commits, off `main`)
- **PR 1 — deterministic leg:** `adjustment.py` (`apply_adjustments` +
  `apply_adjustments_to_config` + `load_sensitivity` + `deterministic_revise`)
  + knob-space/domain validation + history extension (`adjustments`,
  `observed_effects`, `is_oscillating`, `no_improvement_for`) + unit tests. No
  LLM, no loop driver yet — landable and tested in isolation.
- **PR 2 — LLM revise leg:** `AgentCore.revise_instruction` +
  `revise_instruction.md` prompt + mock-agent test.
- **PR 3 — loop driver + DevkitConfig (#47):** `DevkitConfig` in
  `FrameworkConfig`, `Pipeline` wires `MetricsCollector`,
  `run_iteration_loop` + `run_and_collect` orchestration, stub-plant
  integration test. Depends on PR 1 + PR 2. **Includes the config_loader
  prerequisite** (nlohmann-free runtime reader or CMake fetch) so the runtime
  no-rebuild fast path actually works on the bare ARM target — may split into a
  PR 3a (config_loader) + PR 3b (loop driver) if it grows.

## Out of scope
- RFC 0001 (thread-pool codegen) and RFC 0002 (context compaction) — P2
  enablers that consume this contract; separate.
- Units renormalization (#46) — `parse_text` percentages vs JSON fractions +
  `topdown_threshold_pct`; left as tracked follow-up, not silently normalized.
- Spike↔production `_parse_topdown_text` duplication — post-loop cleanup.
- Memory-bandwidth collection in the loop (devkit top-down text has no
  bandwidth; backend_bound is the memory proxy, as in the spike). A separate
  bandwidth collector is a later enhancement.
- Multi-knob joint adjustment (the deterministic controller is OAT, one knob per
  iteration, like the spike). Joint search is a later refinement once OAT
  convergence behavior is observed.

## Open question resolved
Loop **runs on ARM** (the plant is ARM; the spike proved it there). Loop *logic*
is dev/unit-tested locally via a **stub comparator + stub collector + mock
agent** (no ARM/devkit/LLM). Confirmed with the user 2026-08-17.
