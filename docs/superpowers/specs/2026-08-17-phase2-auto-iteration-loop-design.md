# Design — Phase 2 auto-iteration loop

- **Date:** 2026-08-17
- **Author:** Jack Wei
- **Related:** RFC 0003 (merged `4e13dff`), PR #49 (ratio wiring), the §4 spike
  (gate PASSED 2026-08-17), follow-up #47 (DevkitConfig)
- **Status:** Controller model approved (user, 2026-08-17); spec under
  precision refinement 2026-08-17 (oscillation / no-improvement / build-failure
  semantics, gate scope, atomic writes, tier ownership, observed_effects
  attribution)

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
instruction     = agent.run_full_chain(profile_json) if agent.is_available() else seed_instruction
history         = IterationHistory(customer_name)
project = generate(instruction); binary = build(project)         # initial structural build
if binary is None: return fail(history)                           # the seed must compile; no recovery here
run_fail_streak = build_fail_streak = 0
last_report = None
pending_build_fix = False        # set when a structural build failed; skips the next run_and_collect
for i in range(max_iter):
    if pending_build_fix:
        # The last structural revision did not compile. Don't run the (dead)
        # binary — revise straight from the last good report, with the
        # compiler stderr now in history so the LLM can self-correct.
        report = last_report
        pending_build_fix = False
    else:
        workload_prof = run_and_collect(binary, instruction["config"])   # may fail (see error model)
        if workload_prof is a RunFailure:
            run_fail_streak += 1
            history.add(IterationRecord(i, failed=True, reason=workload_prof.reason))
            if run_fail_streak >= config.comparison.run_failure_stop: break   # terminate
            continue                                                          # skip this round, no revise
        run_fail_streak = 0
        report   = compare(customer_profile, workload_prof)
        last_report = report
    priority = decide_iteration_priority(report, config.comparison)
    history.add(IterationRecord(i, report, accepted, score=score(report)))
    if report.convergence.converged: break
    # Degraded mode: decide_iteration_priority still runs NORMALLY and may
    # return >= 2; an unavailable agent does NOT lower it. But the execution
    # path is forced to the runtime tier (deterministic) regardless of
    # priority. priority >= 2 in degraded mode does NOT mean "stop" — only a
    # runtime-tier stall stops (see Agent-unavailable degradation).
    tier = "runtime" if priority == 1 else "structural"
    cand = (deterministic_revise(report, sensitivity, history)        if priority == 1
            else (agent.revise_instruction(...)[1]                    if agent.is_available()
                  else deterministic_revise(report, sensitivity, history)))  # degraded
    if priority >= 2 and not agent.is_available(): history.degraded = True
    accepted, rejected = validate_adjustments(cand, instruction, report, sensitivity, tier)  # gate
    log(rejected)
    if accepted:
        apply_adjustments(instruction, accepted)
        if priority == 1:
            write_config_json_atomic(project/"config.json", instruction["config"])  # reuse binary (atomic)
        else:
            new_binary = build(generate(instruction))                            # rebuild
            if new_binary is None:                                       # structural build failure (see error model)
                build_fail_streak += 1
                history.add(IterationRecord(i, build_failed=True, stderr=build_stderr))
                pending_build_fix = True            # next iter revises from last_report, not a dead binary
                if build_fail_streak >= config.comparison.build_failure_stop: break   # terminate
                continue                             # LLM self-corrects next round (history carries stderr)
            binary = new_binary
            build_fail_streak = 0
    if is_oscillating(history, config.comparison.oscillation_window) \
       or no_improvement_for(history, config.comparison.no_improvement_stop): break
return PipelineResult(best_iteration=history.best_iteration, history=history,
                      degraded=history.degraded)
```

`run_and_collect` mirrors the spike's proven orchestration (`run_one_point` in
`examples/steerability_spike.py`): taskset-pin the binary, warm up, collect
topdown for `measurement_seconds` with `-p <pid>`, wait for exit. The spike is
the reference implementation for this leg.

### `run_and_collect` error model

`run_and_collect` returns either a workload `Profile` or a `RunFailure(reason,
kind)`. Three failure kinds, each with an explicit policy (retry / skip-this-
round / terminate-loop):

- **Workload crash** (`proc.poll() is not None` during warmup, or non-zero exit
  — the spike already surfaces this as `workload_exited_during_warmup`): no
  retry (a crash is deterministic, not transient). → **skip this round**, record
  `failed=True`, no revise. A streak of `run_failure_stop` consecutive crashes
  (likely a non-running instruction from a bad structural revision) →
  **terminate**, return best. The crash stderr/stdout is captured into the
  record for debugging.
- **Timeout** (workload hangs, or `collect_topdown` hits its
  `duration+30` timeout): retry up to `collect_retry` time(s) (could be a
  transient scheduler/devkit hiccup). If it still times out → **skip this
  round**. Streak → terminate.
- **Collection failure** (devkit non-zero rc, or `_parse_topdown_text` finds no
  L1 lines — a format change): retry up to `collect_retry`. Persistent →
  **skip this round**. Streak → terminate.

A skipped round does **not** reset `best_iteration` and does **not** count as
no-improvement (it's an infra failure, not a control failure) — but it does
count toward `run_failure_stop`. The streak resets to 0 on any successful
collect. This keeps the loop from burning budget retrying a broken plant while
not penalizing a single transient hiccup.

### Structural build-failure streak

A structural-tier revision that does not compile (`build()` returns `None`) is
**not** an immediate-terminate. LLM-generated code occasionally has a transient
compile error, and the compile diagnostics are the feedback the LLM needs to
self-correct. Mirroring the run-failure model:

- On a structural build failure: do **not** run (no binary to run), do **not**
  revise-and-break. Record `build_failed=True` + the captured compiler
  stderr/stdout into the `IterationRecord`, increment `build_fail_streak`, and
  `continue` to the next loop iteration. The next `revise_instruction` call
  receives that stderr in `history` (the prior-iteration context), so the LLM
  fixes the specific error rather than re-rolling blindly.
- `build_fail_streak` is bounded by `config.comparison.build_failure_stop`
  (default 2): `>= build_failure_stop` consecutive structural build failures →
  **terminate**, return best. This bounds a persistently-broken LLM without
  killing the loop on one bad generation.
- The streak resets to 0 on any successful build. A runtime-tier iteration
  (no rebuild) never touches this streak. The **initial** seed build
  (`binary is None` before the loop) is *not* retried — the caller-supplied /
  agent-generated seed must compile; a broken seed is a caller error, returned
  as `fail(history)` immediately.
- A build-failed round does **not** count as no-improvement (infra failure, like
  a run crash) and does **not** reset `best_iteration`.

Rationale for not retrying a build *within* the same iteration: a deterministic
syntax error would loop forever; instead the loop advances, hands the LLM the
diagnostics, and lets the *next* revise correct it — bounded by the streak.

### Oscillation detection (precise rule)

`is_oscillating(history, oscillation_window)` needs an exact algorithm, not just
"knob toggled back-and-forth." Track, per knob, the **signed direction** of
each applied move: `sign(Δ)` where `Δ = to − actual_current` at apply time
(`+1` increase, `−1` decrease, `0` no-op/excluded). A **direction reversal**
for a knob in the window is: the knob was applied this iteration with sign `s`,
and it was also applied at least once earlier within the `oscillation_window`
iterations with sign `−s`.

- **Runtime knobs** (cheap iterations, no rebuild): **a single direction
  reversal within the window on any one runtime knob** → oscillation detected →
  terminate. Strict, because a runtime ping-pong is a clear dead end and each
  iteration is cheap to have wasted.
- **Structural knobs** (expensive iterations, regenerate+rebuild): the
  judgment is **relaxed** — require a **full ping-pong** (two reversals: the
  knob moved `A→B`, then back toward `A`, i.e. signs `+−+` or `−+−` within the
  window) **and** the round did not improve `score`. Rationale: a single
  structural reversal is often a legitimate course-correction after seeing the
  metric move the wrong way; only a complete back-and-forth that bought nothing
  signals the structural knob is a dead lever at this operating point.
- **Scope:** oscillation is **per-knob** — any single knob (runtime or
  structural, each under its own rule) triggering is sufficient to stop the
  whole loop. It is **not** required that multiple knobs oscillate
  simultaneously.
- A knob adjusted twice in the **same** direction (sign unchanged) is
  continuation, **not** a reversal — does not count. A no-op round (no accepted
  adjustments) is invisible to the reversal tracker.

This keeps runtime tuning from pinging a dead knob while giving structural
moves one self-correcting step before declaring a saddle — matching the cost
asymmetry of the two tiers.

### Agent-unavailable degradation

When `agent.is_available()` is False (no API key — local-only / CI):

- The Phase-1 initial instruction falls back to a caller-provided
  `seed_instruction` (the loop requires one in degraded mode; no agentless
  instruction synthesis).
- On a **structural** priority (≥2), the loop does **not** terminate.
  It **degrades to runtime-tier-only**: keeps iterating with
  `deterministic_revise` on the runtime knobs for as long as they improve the
  metric (the runtime tier needs no LLM). `history.degraded = True` so the
  result is honestly marked.
- Degraded mode stops when the runtime tier stalls — `no_improvement_stop`,
  `is_oscillating`, or all runtime knobs are skip-blocked (no levers left) —
  *not* merely because a structural priority arose. The structural gap itself
  can only be closed by the LLM, so a degraded run that stalls on a structural
  gap returns `best_iteration` + `degraded=True` + `stop_reason="runtime_tier_
  exhausted_agent_unavailable"`.

This lets local-only / no-API-key runs still make progress via runtime tuning
while never pretending to fix a structural gap it can't.

**Clarification (to avoid an implementation trap):** in degraded mode
`decide_iteration_priority` still executes **normally** and will legitimately
return `≥ 2` when a structural gap is the largest error. That priority is **not**
a stop signal. What changes in degraded mode is only the *execution path* — it
is fixed to the runtime tier (`deterministic_revise`) regardless of the
priority value, because that is the only lever available without the LLM. The
loop continues at the runtime tier until the runtime tier itself stalls
(`no_improvement_stop`, `is_oscillating`, or all runtime knobs skip-blocked),
at which point it stops with `degraded=True` + the
`runtime_tier_exhausted_agent_unavailable` stop reason. An implementer must
**not** read `priority >= 2` as "terminate" in degraded mode — only the
runtime-tier stall conditions terminate.

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

### Atomic `config.json` writes (runtime tier)

The runtime fast path overwrites `project/config.json` every runtime-tier
iteration. On a bare/embedded ARM target the filesystem's crash tolerance is
weak, and a write interrupted by a crash, power loss, or signal would leave a
truncated/corrupt `config.json` — every subsequent iteration then reads garbage
and fails, cascading into `run_failure_stop` for the wrong reason. So
`write_config_json_atomic(path, config)` must be **crash-safe**:

1. serialize `config` to a temp file in the **same directory** as the target
   (`path.parent / f".{path.name}.{pid}.tmp"`) — same-directory so the final
   move is a `rename(2)` on the same filesystem (atomic, not a cross-device
   copy);
2. `fsync` the temp file before closing (durability on real HW; a no-op stub
   where unavailable);
3. `os.replace(tmp, path)` — the atomic rename. On POSIX this is atomic; the
   binary either reads the fully-old or fully-new `config.json`, never a
   half-written one.

This mirrors standard crash-safe config-write practice and is a cheap guarantee
that a single interrupted write can't poison the whole run. The integration
test asserts the temp-then-rename sequence (no in-place truncate).

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

**`from` field semantics** — `from` is **advisory/provenance only**: the value
the adjustment's author *believed* the knob held (for inspectability and
debugging). It is **never** the authoritative base — the authoritative base is
the instruction's actual current value for `(stage, knob)`. `to` is an
**absolute target**. When `from` ≠ actual current value (a "stale-base"
mismatch — a strong LLM-hallucination or stale-context signal):
1. log a warning recording `(knob, from, actual_current, to)`;
2. the validation gate below re-derives the *effective move* as
   `actual_current → to` (NOT `from → to`) and direction-checks that;
3. if `actual_current → to` passes the direction gate, apply `to` (the author's
   absolute target stands); if it violates the sensitivity direction, reject.
So `from` can never cause a wrong-direction apply — the real check is always
against the live state, and `from` only ever produces a warning. (For the
deterministic controller, `from` is set to the actual current value it read, so
mismatches are an LLM-only phenomenon.)

Location: new `src/agent/adjustment.py` (keeps `strategy.py` for
`decide_iteration_priority`).

### 1b. Adjustment validation gate (runs BEFORE `apply_adjustments`)

`validate_adjustments(adjustments, instruction, report, sensitivity, tier)
-> (accepted, rejected)` — a deterministic gate every adjustment list passes
through, whether emitted by the deterministic controller (defensive) or the LLM
(the main defense against hallucination). `tier` is `"runtime"` or
`"structural"`, set by the loop driver from `decide_iteration_priority`
(priority 1 → runtime, ≥2 → structural). For each adjustment, against the
instruction's **actual current value** of `(stage, knob)`:

- **Domain**: `knob` in the named knob space; `to` within the knob's valid
  domain (enum / numeric bounds). Reject otherwise.
- **Tier ownership (enforces the two-tier boundary):** the LLM owns *structural*
  knobs; the deterministic controller owns *runtime* knobs. On a **structural**
  iteration (`tier == "structural"`), any *runtime* knob adjustment
  (`compute_ratio`/`memory_ratio`/`thread_count`/`qps`) is **dropped** with
  `reason: "runtime_knob_not_owned_on_structural_tier"` — the LLM may not retune
  runtime params during a structural revision; the deterministic controller
  will tune them on the next runtime pass. On a **runtime** iteration, only
  runtime-knob adjustments are expected (the deterministic controller never
  emits structural ones, but defensively a structural-knob adjustment on a
  runtime tier is dropped with `reason: "structural_knob_not_owned_on_runtime_tier"`
  — it would require an un-budgeted rebuild). This keeps the realism/assist
  boundary hard: structural realism stays LLM-owned, runtime tuning stays
  deterministic-owned, and neither tier silently crosses.
- **Direction (the hallucination guard)**: the effective move
  `actual_current → to` must move the adjustment's `expected_metric` in the
  direction that **reduces that metric's current error**. This check is
  **independent of which metric is the largest error** — it applies to
  *whatever* `expected_metric` the adjustment declares:
  - Look up the knob's `expected_direction` on its `expected_metric` in the
    sensitivity table (e.g. `working_set_mb` → `backend_bound` `up`).
  - Determine the metric's current error sign from the report: is
    `expected_metric` **too high** or **too low** vs target.
  - If the error is "too high", the knob must move in the direction that
    *lowers* it — so an `up`-direction knob must be *decreased*
    (`to < actual_current`); a `down`-direction knob must be *increased*.
    Symmetric for "too low".
  - If the metric is already within threshold → reject with
    `reason: "metric_already_satisfied"` (no pointless churn).
  - Otherwise, if the effective move's sign matches the error-reducing
    direction → **accept**; if it would move the metric the *wrong* way
    (widen the error) → reject with `reason: "wrong_direction"`.
  This deliberately does **not** require `expected_metric` to be the largest
  error. The deterministic controller *selects* the largest-error metric (its
  own policy, §3); the *gate* accepts any adjustment whose target metric is
  unsatisfied and whose direction reduces that metric's error — so an LLM may
  legitimately work a secondary unsatisfied metric, and is only rejected when
  the target is already satisfied or the direction widens the error.
- **`from` mismatch**: warn (per §1) but do not reject on mismatch alone;
  the direction check uses `actual_current → to`.

Rejected adjustments are logged (with knob + reason) and dropped. Accepted
adjustments flow to `apply_adjustments`. If **all** are rejected (e.g. the LLM
hallucinated a whole batch against the table), the iteration becomes a no-op:
the loop records `adjustments_rejected` and treats it as no-improvement (feeds
the `no_improvement_stop` counter, can trigger stop). Pure, unit-testable.

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
3. Skip any knob toggled in the last `oscillation_window` iterations (history)
   to avoid oscillation; if all candidates are skip-blocked, return `[]` (forces
   escalation to the LLM tier, or — in degraded mode — stalls the runtime tier).
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
reused. `is_available()` gates this — when the agent is unavailable, the loop
**degrades** rather than stopping (see "Agent-unavailable degradation" above:
runtime-tier-only continues; a structural priority does not terminate the loop
on its own). A real `revise_instruction` LLM error (transient retries exhausted)
on a structural tier *is* a stop reason — distinct from "agent not configured".

### 5. Config plumbing — `DevkitConfig` (#47) + loop-control knobs in `ComparisonConfig`

Add to `FrameworkConfig`:
```python
class DevkitConfig(BaseModel):
    devkit_cmd: str | None = None
    duration_seconds: int = 20
    interval_seconds: int = 3
    cpu_range: str | None = None        # taskset pin, e.g. "4"
    collect_pid: bool = True            # -p <pid> attribution (spike-proven)
```
Extend `ComparisonConfig` with the loop-control thresholds (previously
hardcoded `window`/`K`/retry counts — now config-driven, overrideable per run):
```python
class ComparisonConfig(BaseModel):
    topdown_threshold_pct: float = 10.0
    memory_threshold_pct: float = 5.0
    coverage_threshold_pct: float = 80.0
    oscillation_window: int = 3        # iters looked back for knob direction reversals
    no_improvement_stop: int = 3        # K consecutive iters w/o a new best score -> stop
    run_failure_stop: int = 2           # consecutive run/collect failures -> stop
    build_failure_stop: int = 2          # consecutive structural build failures -> stop
    collect_retry: int = 1             # retries on a transient collect/timeout
```
`Pipeline` constructs `MetricsCollector(devkit_cmd=config.devkit.devkit_cmd,
perf_cmd=...)` and `run_and_collect` calls `collect_topdown(path,
duration=config.devkit.duration_seconds, interval=config.devkit.interval_seconds,
pid=proc.pid)`. `duration_seconds` also retires the cosmetic
`BuildRunner.duration_seconds` never-set field (#48) — one source of truth.

**`best_iteration` scoring** (replaces the raw `sum(abs(topdown_diffs))` heuristic
currently in `IterationHistory.add_record`, which ignored memory + coverage):
each dimension is normalized by its threshold so topdown/memory/coverage are
unit-comparable, then summed — **lower is better**, `best_iteration =
argmin`:
```
score(r) = Σ_{m∈topdown_l1} |diff_pct(m)| / topdown_threshold_pct
         + |memory_diff_pct| / memory_threshold_pct
         + max(0, coverage_threshold_pct - coverage_pct) / coverage_threshold_pct
```
A converged iteration scores 0 on every exceeded-threshold term → naturally
wins. Recorded on each `IterationRecord` as `score` for inspectability.

### 6. Loop driver + run/collect orchestration
`Pipeline.run_iteration_loop(flamegraph_path, topdown_path, customer_name,
instruction=None, max_iter=10, no_improve_stop=3) -> PipelineResult`. Owns the
two-tier loop above. `run_and_collect(binary, config)` reuses the spike's
taskset-launch → warmup → `collect_topdown(-p pid)` → wait pattern, factored so
the integration test can inject a stub. Closes #47 by reading DevkitConfig.

### 7. History extension
`IterationRecord` gains `adjustments: list[dict]` and
`observed_effects: dict[metric, float]` (the next iteration's metric delta for
each `expected_metric` the adjustment declared). `IterationHistory` gains
`recent_adjustments(n)` and the two stop predicates the loop driver consults:

- **`no_improvement_for(K)`** — precise definition: **K consecutive iterations
  that did not refresh the historical best `score`** (i.e. no iteration in the
  streak set a new `min(score)`). This is the **"did not beat the best-so-far"
  semantics**, *not* "score rose vs the immediately-previous round" — a round
  that is worse than the previous but still not a new best counts toward the
  streak; a round that refreshes the best resets the streak to 0. **Infra
  failures do not count:** run-failed and build-failed rounds have no measured
  score and must not masquerade as "no improvement" — they advance the
  `run_failure_stop`/`build_failure_stop` streaks instead, not this one.
  **All-adjustments-rejected rounds *do* count:** they ran successfully and have
  a score (they just applied nothing), so a non-best-refreshing all-rejected
  round advances this streak naturally — this is the bounded-hallucination
  behavior (an LLM that keeps emitting table-violating adjustments is bounded
  by `no_improvement_stop`).
- **`is_oscillating(window)`** — the precise algorithm in "Oscillation
  detection (precise rule)" above: per-knob signed-move tracking; a runtime
  knob needs one direction reversal in the window, a structural knob needs a
  full ping-pong (`+−+`/`−+−`) plus no score improvement; any single knob
  tripping its rule fires the stop.

`best_iteration` is `argmin(score)` over all non-failed records (failed /
build-failed records are excluded — they have no measured score and must not
win `best_iteration`).

**`observed_effects` attribution rule.** The deterministic controller is OAT
(one knob per iteration), so a single-adjustment round's `observed_effects`
**is** attributable to that one knob: record `{expected_metric: Δ_metric}` =
`(next_report[metric] − this_report[metric])`. But the LLM may emit a **batch**
of multiple structural adjustments in one round — the metric delta is then the
*joint* effect of all of them and cannot be decomposed per-knob. For
multi-adjustment rounds, `observed_effects` records the **overall** metric
deltas only (no per-knob attribution); the per-knob `adjustments` list is still
kept on the record for inspectability, but the `observed_effects` map is used
**only for trend/oscillation judgment** (did the batch move things the right
way overall), not to credit a single knob. This matches the OAT limitations
already documented and keeps multi-knob batches from fabricating false
per-knob causality.

## Termination
- `report.convergence.converged` (comparator thresholds: topdown < 10%,
  memory < 5%, coverage > 80% from `ComparisonConfig`) → **success**.
- `max_iter` reached → stop, return `history.best_iteration` (by `score`).
- **Oscillation** (`is_oscillating(oscillation_window)` — runtime knob: one
  direction reversal; structural knob: full ping-pong + no improvement) → stop,
  return best.
- **No improvement for `no_improvement_stop`** consecutive iterations (no new
  best `score` — see §7; run-failed/build-failed rounds excluded as infra
  failures, all-rejected rounds included as a control failure) → stop, return
  best.
- **Run-failure streak** `>= run_failure_stop` consecutive run/collect failures
  → stop (broken plant / non-running instruction), return best.
- **Build-failure streak** `>= build_failure_stop` consecutive structural build
  failures → stop (persistently broken LLM generation), return best. A single
  build failure does **not** stop — the LLM gets the compiler diagnostics and a
  self-correcting next iteration.
- **Degraded-mode stall** (agent unavailable + runtime tier exhausted: all
  runtime knobs skip-blocked, or no-improvement/oscillation while a structural
  gap remains) → stop, return best + `degraded=True`. Note: a structural
  priority (`>= 2`) in degraded mode is **not** itself a stop — only the
  runtime-tier stall stops.
- **All-adjustments-rejected iteration** counts as no-improvement (feeds
  `no_improvement_stop`) — an LLM that keeps hallucinating against the table is
  bounded and surfaced, not looped forever.

Do not burn budget on a non-converging run; every stop returns `best_iteration`
by `score` so partial progress is never lost.

## Tests
- `apply_adjustments`: structural + runtime routing by `(stage, knob)`,
  domain validation (enum, bounds), raises on unknown stage/knob, idempotent on
  `from==to`, and the `from`≠actual path (warns, still applies `to` if the
  direction gate passes). Pure unit tests, no LLM/devkit.
- `validate_adjustments` (the gate): rejects wrong-direction moves, rejects
  already-satisfied-metric adjustments, warns-but-keeps `from` mismatches,
  re-derives the effective move against `actual_current`, and returns
  `accepted=[]` when all are rejected. Pure unit tests.
- `validate_adjustments` **tier ownership**: on a structural-tier call, a
  runtime-knob adjustment is dropped (`runtime_knob_not_owned_on_structural_tier`);
  on a runtime-tier call, a structural-knob adjustment is dropped
  (`structural_knob_not_owned_on_runtime_tier`). Pure unit tests.
- `validate_adjustments` **direction scope**: an adjustment targeting a
  *secondary* (not largest) unsatisfied metric in the correct reducing
  direction is **accepted** (gate is not largest-error-coupled); one in the
  wrong direction is rejected. Pure unit tests.
- `deterministic_revise`: given a fake report + sensitivity table, picks the
  correct runtime knob in the correct direction; respects history
  (skip-blocked knobs force `[]` → escalation); clamps to bounds; never emits a
  structural knob. Pure unit tests.
- `load_sensitivity`: parses the spike's real `sensitivity.json` fixture →
  correct entries + `expected_direction`.
- `score` / `best_iteration`: a fixture of `IterationRecord`s asserts the
  normalized multi-dim score ranks correctly (a converged iter scores lowest;
  ties broken by iteration order); failed/build-failed records are excluded
  from `best_iteration`. Replaces the old raw-sum heuristic.
- `is_oscillating`: a fixture history where (a) a runtime knob reverses once →
  oscillation fires; (b) a structural knob reverses once → does NOT fire; (c) a
  structural knob ping-pongs (`+−+`) with no score improvement → fires; (d) two
  same-direction moves → does NOT fire. Pure unit tests.
- `no_improvement_for`: streak counts iterations that fail to set a new best
  `score`; a worse-than-previous-but-not-best round advances the streak; a
  new-best resets it; failed/build-failed/all-rejected rounds do NOT advance
  it. Pure unit tests.
- `observed_effects` attribution: a single-adjustment round records per-knob
  `{metric: Δ}`; a multi-adjustment (LLM batch) round records **overall** deltas
  only, no per-knob decomposition. Pure unit tests.
- `revise_instruction`: mock-agent (recorded JSON response) — asserts the
  revised instruction carries the emitted adjustments and that the gate catches
  a deliberately-wrong-direction hallucinated adjustment. No real LLM call.
- **`run_and_collect` error model**: a stub runner/collector that injects
  crash / timeout / collect-fail → asserts retry-then-skip, streak counting,
  `run_failure_stop` termination, and that a successful collect resets the
  streak. No ARM/devkit.
- **`write_config_json_atomic`**: asserts temp-file-in-same-dir → `os.replace`
  sequence (no in-place truncate), and that a partially-written temp never
  becomes the live `config.json`. Pure unit test with a fake FS / monkeypatch.
- **Build-failure streak**: a stub builder that returns `None` once then
  succeeds → asserts the loop records `build_failed`, does NOT terminate, and the
  next `revise_instruction` receives the stderr; a stub that fails
  `build_failure_stop` times → terminates with best. No ARM/devkit.
- **Loop driver integration test**: a **stub plant** — inject a fake
  `collect_topdown` (returns a Profile whose metrics move deterministically
  toward target as knobs adjust) + a mock agent + the real deterministic
  controller + the real gate. Asserts: converges within N iters; escalates to
  LLM tier on a structural gap; stops on oscillation / no-improvement; returns
  `best_iteration` by `score`; **degraded mode** (mock agent unavailable)
  continues runtime-only, keeps running through `priority >= 2` (does NOT
  stop on a structural priority alone), and stops with `degraded=True` only on a
  runtime-tier stall. Runs locally with no ARM/devkit/LLM — this is the
  dev/test path confirmed with the user.
- Real-run validation (user-side, on ARM): one `run_iteration_loop` against the
  devkit collector + real LLM, mirroring the spike's box.

## PR structure (relevant-only commits, off `main`)
- **PR 1 — deterministic leg + gate:** `adjustment.py` (`apply_adjustments` +
  `apply_adjustments_to_config` + `load_sensitivity` + `deterministic_revise` +
  `validate_adjustments` (incl. tier-ownership + direction-scope checks) +
  `score`) + knob-space/domain validation + history extension
  (`adjustments`, `observed_effects` w/ multi-adjustment attribution, `score`,
  `is_oscillating` (precise rule), `no_improvement_for` (best-refresh
  semantics), `best_iteration` excludes failed records) + `ComparisonConfig`
  loop-control knobs (`oscillation_window`, `no_improvement_stop`,
  `run_failure_stop`, `build_failure_stop`, `collect_retry`) + unit tests. No
  LLM, no loop driver yet — landable and tested in isolation.
- **PR 2 — LLM revise leg:** `AgentCore.revise_instruction` +
  `revise_instruction.md` prompt + mock-agent test (incl. gate-catches-
  hallucination assertion + tier-ownership: LLM emitting a runtime knob on a
  structural tier is dropped).
- **PR 3 — loop driver + DevkitConfig (#47):** `DevkitConfig` in
  `FrameworkConfig`, `Pipeline` wires `MetricsCollector`,
  `run_iteration_loop` + `run_and_collect` orchestration + the **error model**
  (crash/timeout/collect-fail → retry/skip/streak-terminate) + the
  **build-failure streak** + `write_config_json_atomic` (crash-safe
  temp→`os.replace`) + **agent-unavailable degradation** + stub-plant
  integration test (incl. degraded-mode path + build-failure path). Depends on
  PR 1 + PR 2. **Includes the config_loader prerequisite** (nlohmann-free
  runtime reader or CMake fetch) so the runtime no-rebuild fast path actually
  works on the bare ARM target — may split into a PR 3a (config_loader) + PR 3b
  (loop driver) if it grows.

## Out of scope
- RFC 0001 (thread-pool codegen) and RFC 0002 (context compaction) — P2
  enablers that consume this contract; separate.
- Units renormalization (#46) — `parse_text` percentages vs JSON fractions +
  `topdown_threshold_pct`; left as tracked follow-up, not silently normalized.
- Spike↔production `_parse_topdown_text` duplication — post-loop cleanup.
- Memory-bandwidth collection in the loop (devkit top-down text has no
  bandwidth; backend_bound is the memory proxy, as in the spike). A separate
  bandwidth collector is a later enhancement.

## Known limitations & risks of OAT single-knob tuning

The deterministic controller adjusts **one knob per iteration** (OAT), matching
the spike. This is a deliberate first cut (cheap, debuggable, matches the
verified sensitivity data) but has documented limitations that a later
refinement must address — recorded here so a non-converging run is debuggable
against them, not a surprise:

- **Cannot reach targets requiring joint moves.** Some target regions need two
  knobs moved together (e.g. raise `compute_ratio` *and* lower `memory_ratio` to
  shift the mix without overshooting retiring). OAT moves one at a time, so it
  may oscillate around such a region or stall on a saddle. → future: joint /
  coordinate-descent adjustment.
- **Knob interactions invalidate the OAT sensitivity table.** The table is OAT
  (each knob swept while others hold defaults). A `compute_ratio` change shifts
  the baseline `working_set_mb` was tuned against, so a previously-correct
  `working_set_mb` may need re-tuning — OAT can chase its own tail. → the
  `no_improvement_stop` / `is_oscillating` guards stop this; future: re-spike
  after large structural moves, or a model-based controller.
- **Step size is fixed.** The bounded step (±0.2 ratio, ±1 thread) may
  under-shoot (slow convergence) or over-shoot (oscillation past the target).
  → future: adaptive step (larger when far, smaller when near threshold).
- **Single largest-error metric drives selection.** OAT picks the knob for the
  single largest error; a run with two near-equal errors may thrash between
  them. → future: weighted multi-metric selection.
- **OAT can't express "hold A, move B then A".** Sequencing is implicit. →
  future: explicit multi-adjustment batches.

These are **deferred optimizations**, not blockers for the first loop: the
termination guards (no-improvement, oscillation, max_iter) bound the cost of
each, and the recorded per-knob observed effects make the failure mode
inspectable. Joint/multi-knob search is the natural Phase-2.1 follow-up once
real OAT convergence behavior is observed on ARM.

## Open question resolved
Loop **runs on ARM** (the plant is ARM; the spike proved it there). Loop *logic*
is dev/unit-tested locally via a **stub comparator + stub collector + mock
agent** (no ARM/devkit/LLM). Confirmed with the user 2026-08-17.
