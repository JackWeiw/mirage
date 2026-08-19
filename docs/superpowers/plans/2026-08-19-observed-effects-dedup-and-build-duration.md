# PR 3b follow-up #2 + #3: observed_effects helper dedup + BuildResult duration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two remaining PR 3b follow-ups: (2) extract the duplicated `observed_effects` attribution logic into a single `_attribute_observed_effects` helper; (3) #48 — populate `BuildResult.duration_seconds` with the real wall-clock build duration instead of leaving it at the default `0.0`.

**Architecture:** (2) The identical attribution block currently appears at two sites in `run_iteration_loop` (the priority==0 converged break-path and the loop tail, before `prev_record = record`). Extract it into a `@staticmethod _attribute_observed_effects(prev_record, last_report, report)` that mutates `prev_record.observed_effects` in place, and call it from both sites. (3) `BuildRunner.build` already runs cmake+make via `subprocess.run`; wrap the whole build in `time.monotonic()` and set `duration_seconds` on every `BuildResult` return (success + all failure paths), so the field is honest telemetry rather than a dead `0.0`.

**Tech Stack:** Python 3.13, pydantic v2, pytest, ruff + mypy strict, pre-commit.

**Scope:** PR 3b follow-up #2 + #3 only. Out of scope: `ExecutionResult.duration_seconds` (separate, not in the follow-up list), real-ARM re-run (user-side).

---

## Task 1: Extract `_attribute_observed_effects` helper

**Files:**
- Modify: `src/harness/pipeline.py` (extract helper near `_applied_moves`; replace both duplicated blocks).

- [ ] **Step 1: Add the helper** — as a `@staticmethod` near `_applied_moves` (line ~473):

```python
@staticmethod
def _attribute_observed_effects(
    prev_record: IterationRecord,
    last_report: dict[str, Any],
    report: dict[str, Any],
) -> None:
    """Attribute observed metric deltas to the PREVIOUS record's adjustments.

    Mutates prev_record.observed_effects in place. Single-adjustment round ->
    per-knob attribution to that adjustment's expected_metric (only if the
    metric is in the new report). Multi-adjustment (LLM batch) -> overall
    deltas across every topdown L1 metric (no false per-knob causality).
    """
    last_td = last_report.get("topdown_l1", {})
    new_td = report.get("topdown_l1", {})
    if len(prev_record.adjustments) == 1:
        metric = str(prev_record.adjustments[0].get("expected_metric", ""))
        if metric and metric in new_td:
            old_diff = last_td.get(metric, {}).get("diff_pct", 0.0)
            new_diff = new_td.get(metric, {}).get("diff_pct", 0.0)
            prev_record.observed_effects[metric] = new_diff - old_diff
    elif len(prev_record.adjustments) > 1:
        for metric in new_td:
            old_diff = last_td.get(metric, {}).get("diff_pct", 0.0)
            new_diff = new_td.get(metric, {}).get("diff_pct", 0.0)
            prev_record.observed_effects[metric] = new_diff - old_diff
```

- [ ] **Step 2: Replace the converged-branch block** (lines ~707-731) with:

```python
# Run attribution for the previous record before breaking.
if prev_record is not None and last_report is not None:
    self._attribute_observed_effects(prev_record, last_report, report)
stop_reason = "converged"
break
```

- [ ] **Step 3: Replace the loop-tail block** (lines ~850-868) with:

```python
# ---- observed_effects attribution (for the PREVIOUS record) ----
if prev_record is not None and last_report is not None:
    self._attribute_observed_effects(prev_record, last_report, report)
prev_record = record
last_report = report  # capture for next iteration's attribution
```

- [ ] **Step 4: Run** — `PYTHONPATH=src python -m pytest tests/harness/test_run_iteration_loop.py -q`. All green (behavior identical).

## Task 2: Populate `BuildResult.duration_seconds`

**Files:**
- Modify: `src/harness/build_runner.py` (wrap build in `time.monotonic`; set on every return).

- [ ] **Step 1: Add `import time`** at the top of `build_runner.py`.

- [ ] **Step 2: Wrap the build body** — capture `start = time.monotonic()` at the top of `build()` and set `duration_seconds=time.monotonic() - start` on every `BuildResult` return (the 3 early failure returns + the cmake-failure + make-failure + success). Concretely, compute `elapsed = time.monotonic() - start` once before each `return` and pass it. For the timeout/FileNotFoundError paths, the elapsed-at-exception time is the honest duration.

- [ ] **Step 3: Run** — `PYTHONPATH=src python -m pytest tests/harness/test_build_runner.py -q`. Existing tests still pass (`duration_seconds` defaults are not asserted; new field is populated).

## Task 3: Tests

**Files:**
- Modify: `tests/harness/test_build_runner.py` (add a duration test).

- [ ] **Step 1: Add `test_build_sets_duration_seconds_on_success`** — fake build dir + a stubbed `subprocess.run` (or use the existing fake-build-dir helper) so `build()` succeeds; assert `result.duration_seconds >= 0.0` and that it is set (not the unmodified default path — assert it is a `float`). The key assertion: on the timeout/failure path, duration is also populated. Add `test_build_sets_duration_seconds_on_failure` covering the cmake-not-found / make-timeout path asserting `duration_seconds` is a non-negative float.
- [ ] **Step 2: Run** — green.

## Task 4: Gate + PR

- [ ] `python -m ruff format src/ tests/ && python -m ruff check src/ tests/ && PYTHONPATH=src python -m pytest tests/ -q && PYTHONPATH=src python -m mypy --config-file=pyproject.toml src/ tests/ && python -m pre_commit run --all-files`
- [ ] Commit + PR `refactor/attribute-observed-effects-and-build-duration`. Two-stage review (spec + quality), squash-merge, delete branch.

## Verification
- All tests green, cov >= 60%, mypy clean, pre-commit clean.
- `observed_effects` attribution behavior unchanged (the two call sites now delegate to one helper).
- `BuildResult.duration_seconds` is populated on every return path of `build()` (no more dead `0.0`).

## Out of scope
- `ExecutionResult.duration_seconds` (separate concern, not in the follow-up list).
- Real-ARM re-run (user-side).
