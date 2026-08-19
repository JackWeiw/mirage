# Build-seam stderr + build-failure self-correction (#3b-followup-1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the loop's build-failure self-correction path actually work — thread real compiler stderr through the `build` seam into `IterationRecord.build_stderr`, surface it in the LLM revise prompt, and on a `pending_build_fix` iteration apply the LLM's *revised instruction* (a code-level fix) rather than knob adjustments (which cannot fix a codegen compile error).

**Architecture:** Three coupled fixes serving one goal. (A) The loop's `build` seam changes from `Callable[..., str | None]` to `Callable[..., BuildResult]` so the real compiler stderr survives; a new `build_workload_result(project_dir) -> BuildResult` preserves it while `build_workload` (other callers) stays `str|None`. (B) `_serialize_recent_history` adds `build_failed` + `build_stderr` so the LLM sees the error. (C) The `pending_build_fix` branch applies the LLM's `_revised` instruction (rebuild from it) instead of gating+applying knob adjustments; the gate is intentionally skipped on this path because it validates *knob* direction/scope, not instruction-structure correctness.

**Tech Stack:** Python 3.13, pydantic v2, pytest, ruff + mypy strict, pre-commit.

**Scope:** PR 3b follow-up #1 only. Out of scope: the `_attribute_observed_effects` dedup (follow-up #2), `BuildResult.duration_seconds` (#48), re-running real-ARM (user-side).

---

## Why

The `pending_build_fix` design intends: on a structural rebuild failure, record the compiler stderr, skip running the dead binary, and revise from the last report WITH the stderr in history so the LLM self-corrects (spec `2026-08-17-phase2-auto-iteration-loop-design.md`, precision pass item 4). It is currently inert for three reasons (the gap this PR closes):

1. `build_workload`/`_build_instruction`/the `build` seam all return `str | None` (binary path), discarding `BuildResult.stderr`. The loop sets `record.build_stderr = "build_returned_none"` — a literal placeholder, never the real error.
2. `_serialize_recent_history` omits `build_failed`/`build_stderr`, so the LLM never sees the error even if it were recorded.
3. The `pending_build_fix` branch discards `_revised` (pipeline.py:699 `_revised, cand = ...` keeps only `cand`) and re-applies knob adjustments. Knob values cannot fix a codegen compile error, so self-correction cannot succeed regardless of (1)+(2).

## Files
- Modify: `src/harness/pipeline.py` — `build_workload_result`, `build_workload` delegate, `build` seam type, `_build_instruction` return type, initial/structural build call sites, `pending_build_fix` apply-revised-instruction path.
- Modify: `src/agent/agent_core.py` — `_serialize_recent_history` adds `build_failed` + `build_stderr`.
- Modify: `tests/harness/test_run_iteration_loop.py` — convert `_fake_build` + `build_fail_after_seed` to return `BuildResult`; add `test_build_failure_records_real_stderr`; add `test_build_failure_self_corrects_via_revised_instruction`; update the existing streak test for the new seam.
- Modify: `tests/agent/test_agent_core.py` — add a `_serialize_recent_history` test asserting `build_stderr` appears when present.

No production change to `models/results.py` (`BuildResult` already has `stderr`) or `observability/iteration_history.py` (`IterationRecord.build_stderr` already a `str` field).

## Task 1: `build_workload_result` + `build` seam returns `BuildResult`

- [ ] **Step 1: Write failing test** — `tests/harness/test_run_iteration_loop.py::test_build_failure_records_real_stderr`: a `build` stub returning `BuildResult(success=False, stderr="error: use of undeclared identifier 'foo'")` on the 2nd call, success otherwise; agent returns a revised instruction; assert the failed record's `build_stderr` contains `"undeclared identifier 'foo'"` (NOT `"build_returned_none"`).

- [ ] **Step 2: Implement**
  - `pipeline.py`: add `build_workload_result(self, project_dir) -> BuildResult` (wraps `build_runner.build()` with the existing telemetry + `logger.error` on failure; returns the full `BuildResult`).
  - Refactor `build_workload(self, project_dir) -> str | None` to `return self.build_workload_result(project_dir).binary_path` (callers at lines ~198/~398 unchanged).
  - `run_iteration_loop`: `build` param type `Callable[[dict[str, Any]], BuildResult]`; `_default_build` returns `self.build_workload_result(pdir)`; `_build_instruction` returns `BuildResult`; initial build `binary_res = self._build_instruction(...); if not binary_res.success: return seed_build_failed (error includes stderr)`; `binary = binary_res.binary_path`.
  - Structural rebuild (line ~741): `new_res = self._build_instruction(instruction, build_fn)`; if `not new_res.success`: `record.build_stderr = new_res.stderr` (replace placeholder); else `binary = new_res.binary_path`.

- [ ] **Step 3: Run** — `PYTHONPATH=src python -m pytest tests/harness/test_run_iteration_loop.py -q`. Update `_fake_build`/`build_fail_after_seed` to return `BuildResult` (success path: `BuildResult(success=True, binary_path="/fake/binary")`; fail path: `BuildResult(success=False, stderr="...")`). Existing tests green.

## Task 2: Surface `build_stderr` in the revise prompt

- [ ] **Step 1: Write failing test** — `tests/agent/test_agent_core.py`: call `_serialize_recent_history` on a fake history whose last record has `build_failed=True, build_stderr="undeclared id"`; assert the JSON string contains `"build_stderr"` and `"undeclared id"`.

- [ ] **Step 2: Implement** — `agent_core.py::_serialize_recent_history`: add `"build_failed": getattr(r, "build_failed", False)` and `"build_stderr": getattr(r, "build_stderr", "")` to each record dict (only when build_failed or non-empty stderr, to avoid clutter).

- [ ] **Step 3: Run** — green.

## Task 3: `pending_build_fix` applies the revised instruction

- [ ] **Step 1: Write failing test** — `test_build_failure_self_corrects_via_revised_instruction`: build stub fails call 2 (stderr) then succeeds call 3+; mock agent returns a `_revised` instruction (a distinct dict) + `cand=[]`; collect returns a small non-converged gap (structural fires); `max_iter=4`. Assert: `build_count >= 3` (recovery happened), `stop_reason == "max_iter"` (NOT `"build_failure_streak"`), and the failed record (iter0) carries the real stderr.

- [ ] **Step 2: Implement** — in `run_iteration_loop`, track a local `apply_revised = False` set in the `pending_build_fix` branch (after `report = last_report`). After the structural revise call (line ~699) where `agent_avail_now`, if `apply_revised and _revised is not None`: set `instruction = _revised`; skip the gate/`apply_adjustments`; do the structural rebuild directly (`_build_instruction(instruction, build_fn)`); on success `binary = new_res.binary_path`, clear `pending_build_fix`; on failure `record.build_failed=True; record.build_stderr=new_res.stderr; pending_build_fix=True; build_fail_streak+=1` (streak still terminates `build_failure_stop`). The record for this iter has `adjustments=[]`. Reset `apply_revised = False` after handling.

- [ ] **Step 3: Run** — green, including the existing `test_build_failure_streak` (mock returns `_revised=instr` unchanged → rebuild still fails → streak terminates).

## Task 4: Gate + PR

- [ ] `python -m ruff format src/ tests/ && python -m ruff check src/ tests/ && PYTHONPATH=src python -m pytest tests/ -q && PYTHONPATH=src python -m mypy --config-file=pyproject.toml src/ tests/ && python -m pre_commit run --all-files`
- [ ] Commit + PR `fix/build-seam-stderr-self-correction`. Two-stage review (spec + quality), squash-merge, delete branch.

## Verification
- All tests green, cov >= 60%, mypy clean, pre-commit clean.
- `record.build_stderr` carries the real compiler error (not a placeholder).
- `_serialize_recent_history` exposes `build_stderr` to the LLM.
- A one-shot build failure followed by an LLM revised-instruction → the loop rebuilds from the fix and continues (no false `build_failure_streak`).
- An *unfixable* build (LLM revises but build keeps failing) still terminates via `build_failure_stop` streak.

## Out of scope
- `_attribute_observed_effects` dedup (follow-up #2).
- `BuildResult.duration_seconds` (#48).
- Real-ARM re-run (user-side).
