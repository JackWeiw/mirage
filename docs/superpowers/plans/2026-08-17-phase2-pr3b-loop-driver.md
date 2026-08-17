# Phase 2 PR 3b: Loop driver + run/collect orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Phase 2 auto-iteration loop: `Pipeline.run_iteration_loop` (two-tier: runtime no-rebuild / structural regenerate+rebuild), `run_and_collect` (background Popen + taskset + `collect_topdown(-p pid)` + wait, mirroring the spike), the run/collect error model (crash/timeout/collect-fail -> retry/skip/streak-terminate), the structural build-failure streak, agent-unavailable degradation, and a stub-plant integration test that exercises the REAL deterministic controller + REAL gate + REAL comparator with only the plant (collect/build) and agent stubbed.

**Architecture:** Per the approved spec (`docs/superpowers/specs/2026-08-17-phase2-auto-iteration-loop-design.md`, branch `docs/phase2-loop-design`). The loop carries the binary across iterations (regenerated only on the structural tier). Each iteration: collect -> compare -> decide_iteration_priority -> tier (priority 1 = runtime/deterministic, >=2 = structural/LLM) -> candidate adjustments (deterministic_revise OR agent.revise_instruction) -> validate_adjustments gate -> apply_adjustments -> (runtime: write_config_json_atomic + reuse binary; structural: rebuild) -> record. Termination: convergence / max_iter / is_oscillating / no_improvement_for / run_failure_stop streak / build_failure_stop streak / degraded stall. Returns PipelineResult(best_iteration, degraded, stop_reason). The loop is dev/unit-tested via a STUB plant (no ARM/devkit/LLM); real run is user-side on ARM.

**Tech Stack:** Python 3.13, pydantic v2, pytest (`pythonpath=["src"]`, `--cov-fail-under=60`), ruff + ruff-format + mypy strict (src/+tests/), pre-commit. **ASCII-only** in all source/test files (Windows GBK locale).

**Spec reference (READ THESE SECTIONS before implementing — they are the source of truth for the verbatim rules):** `git show origin/docs/phase2-loop-design:docs/superpowers/specs/2026-08-17-phase2-auto-iteration-loop-design.md`. Sections: "Two-tier loop driver" (pseudocode), "run_and_collect error model", "Structural build-failure streak", "Oscillation detection (precise rule)", "Agent-unavailable degradation", "Termination". This plan provides the seams, signatures, and test scaffolding; the spec provides the exact semantics.

**PR 3a dependencies (merged before PR 3b starts):** `DevkitConfig` (`config.devkit.{devkit_cmd,duration_seconds,interval_seconds,cpu_range,collect_pid}`), `write_config_json_atomic(path, config)` (`src/harness/config_writer.py`), nlohmann-free `config_loader.h.j2`.

**Existing pieces (verified by reading the code — do not re-derive):**
- `Pipeline.__init__` (`src/harness/pipeline.py`): constructs `MetricsCollector()` with NO args. **PR 3b Task 2 wires** `MetricsCollector(devkit_cmd=self.config.devkit.devkit_cmd)`. Also constructs `self.generator`, `self.build_runner`, `self.comparator` (`ProfileComparator(config=self.config.comparison)`), `self.agent`, `self.history` (`IterationHistory`).
- `decide_iteration_priority(report, config=ComparisonConfig)` (`src/agent/strategy.py`): returns 0 converged, 1 config-only/runtime, 2/3/4 structural. **Tier mapping: priority==1 -> "runtime"; priority>=2 -> "structural".**
- `IterationHistory` (`src/observability/iteration_history.py`) PR 1 API (already merged): `compute_score(record, comparison)`, `add_record(record)` (excludes failed/build_failed from best_iteration, sets score if None), `recent_adjustments(n)`, `no_improvement_for(k)` (best-refresh semantics; failed/build_failed skipped; all-rejected counted), `is_oscillating(window)` (per-knob signed moves from `applied_moves`; runtime=1 reversal, structural=full ping-pong+no-improve), `best_iteration`, `degraded` field. `IterationRecord` fields: `iteration, converged, topdown_diffs, memory_diff_pct, coverage_pct, strategy_priority, score, adjustments, applied_moves, observed_effects, failed, build_failed, failure_reason, build_stderr`.
- `adjustment.py` (PR 1, merged): `apply_adjustments(instruction, adjustments) -> instruction`, `apply_adjustments_to_config(...)` (runtime subset writer — but PR 3b uses `write_config_json_atomic` for crash-safety), `load_sensitivity(path)`, `validate_adjustments(adjustments, instruction, report, sensitivity, tier) -> (accepted, rejected)`, `deterministic_revise(instruction, report, sensitivity, history) -> list[adjustment]`. `KNOB_DOMAINS`, runtime knobs = {compute_ratio, memory_ratio, thread_count, qps}; structural knobs = {working_set_mb, access_pattern, iterations, archetype}.
- `AgentCore.revise_instruction(prior_instruction, report, sensitivity, history) -> (revised_instruction, list[adjustment])` (PR 2, merged). `agent.is_available()`.
- `ProfileComparator.compare(customer_profile, workload_profile, iteration) -> report` (`src/profile/comparator.py`). Report shape: `report["topdown_l1"][metric]["diff_pct"]/["within_threshold"]`, `report["convergence"]["converged"]/["reason"]`, `report["hotspot_coverage"]["coverage_pct"]`, `report["memory"]["bandwidth_gbps"]["diff_pct"]`.
- `Profile`/`TopdownL1` (`src/profile/profile_schema.py`): `Profile(metadata=ProfileMetadata(customer=...), topdown=TopdownL1(frontend_bound=..., backend_bound=..., bad_speculation=..., retiring=...), memory=MemoryProfile(bandwidth_gbps=...), hotspots=[...])`.
- `MetricsCollector.collect_topdown(output_path, duration, interval, pid)` (`src/harness/metrics_collector.py`, PR #45): runs `devkit tuner top-down -d .. -i .. [-p ..]`, writes TEXT report to output_path, returns `CollectionResult(success, topdown_path, error)`. `parse_topdown_file(path) -> Profile`.
- `BuildRunner.build(project_dir) -> BuildResult(success, stdout, stderr, binary_path)` (`src/harness/build_runner.py`). `WorkloadGenerator().generate(instruction, output_dir) -> Path`.
- Spike `examples/steerability_spike.py::run_one_point` is the reference for `run_and_collect`: `launch_cmd = [binary, config_path]` or `["taskset","-c",cpu_range, binary, config_path]`; `proc = subprocess.Popen(launch_cmd, cwd=project_dir, stdout=PIPE, stderr=PIPE, text=True)`; `time.sleep(warmup_seconds)`; `if proc.poll() is not None:` -> crash during warmup (capture stdout/stderr/rc); `collect_topdown(measurement, interval, proc.pid)`; `proc.wait(timeout=measurement + _PAD_S + 30)` (pad=3); `proc.kill()` on wait-timeout; parse topdown -> if no L1 lines -> collect-fail. `_WORKLOAD_MEASUREMENT_PAD_S = 3`.

**Out of scope:** real ARM run (user-side), RFC 0001/0002, units renormalization (#46), spike<->production `_parse_topdown_text` dedup, memory-bandwidth collector in-loop (backend_bound is the memory proxy, as in the spike).

---

## File Structure

- **Modify:** `src/models/results.py` — add `RunFailure` model; extend `PipelineResult` with `degraded`, `best_iteration`, `stop_reason`, `history_path`.
- **Modify:** `src/harness/pipeline.py` — wire `MetricsCollector(devkit_cmd=...)`; add `run_and_collect(...)`, `run_iteration_loop(...)`. (Import `RunFailure`, `write_config_json_atomic`, `deterministic_revise`, `validate_adjustments`, `apply_adjustments`, `load_sensitivity`, `compute_score`, `TopdownL1`/`Profile`/`ProfileMetadata`.)
- **Create:** `tests/harness/test_run_and_collect.py` — run/collect error-model unit tests (monkeypatched Popen + collect_topdown).
- **Create:** `tests/harness/test_run_iteration_loop.py` — stub-plant integration tests (all paths).

**Injectable seams (for testability — the integration test stubs the PLANT only, keeps the real controller/gate/comparator):**
- `run_iteration_loop(..., collect=None, build=None, sensitivity=None)`:
  - `collect: Callable[[str, dict], Profile | RunFailure] | None` — default wraps `self.run_and_collect(binary, project_dir, warmup, measurement)`; the stub returns a `Profile` whose metrics move with the instruction's knobs (no real Popen/devkit).
  - `build: Callable[[dict], str | None] | None` — default wraps `self.generator.generate + self.build_runner.build`; the stub returns a fake binary path (or `None` to inject a build failure for the streak test).
  - `sensitivity: dict[str, dict] | None` — default `load_sensitivity(sensitivity_path)` if a path is configured else `{}`; tests pass a dict directly.

---

### Task 1: RunFailure model + PipelineResult extension

**Files:**
- Modify: `src/models/results.py` (add `RunFailure`; extend `PipelineResult`)
- Test: `tests/models/test_results.py` (create if absent, else append)

- [ ] **Step 1: Write the failing tests**

Create/append `tests/models/test_results.py`:

```python
"""Tests for result models (RunFailure, extended PipelineResult)."""

from models.results import PipelineResult, RunFailure


def test_run_failure_defaults() -> None:
    rf = RunFailure(reason="crash", kind="crash")
    assert rf.reason == "crash"
    assert rf.kind == "crash"
    assert rf.stdout == ""
    assert rf.stderr == ""


def test_pipeline_result_loop_fields() -> None:
    # The loop driver returns best_iteration, degraded, stop_reason, history_path.
    r = PipelineResult(
        success=True,
        best_iteration=3,
        degraded=False,
        stop_reason="converged",
        history_path="/tmp/history.json",
    )
    assert r.best_iteration == 3
    assert r.degraded is False
    assert r.stop_reason == "converged"
    assert r.history_path == "/tmp/history.json"
    # Existing Phase-1 fields still work.
    assert r.success is True
    assert r.error == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/models/test_results.py -q`
Expected: FAIL (`RunFailure` undefined; `PipelineResult` missing the new fields).

- [ ] **Step 3: Write minimal implementation**

In `src/models/results.py`, add after `CollectionResult`:

```python
class RunFailure(BaseModel):
    """A run/collect failure from run_and_collect (crash / timeout / collect_fail)."""

    reason: str
    kind: str  # "crash" | "timeout" | "collect_fail"
    stdout: str = ""
    stderr: str = ""
```

Extend `PipelineResult` (add fields; keep existing ones):

```python
class PipelineResult(BaseModel):
    """Result of a full pipeline run."""

    success: bool
    customer_profile_json: str | None = None
    comparison_report: dict[str, object] | None = None
    project_dir: str | None = None
    error: str = ""
    # Phase 2 auto-iteration loop fields.
    best_iteration: int | None = None
    degraded: bool = False
    stop_reason: str = ""
    history_path: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/models/test_results.py tests/agent/test_pipeline.py -q`
Expected: PASS (new tests + existing pipeline tests that construct PipelineResult still green — new fields have defaults).

- [ ] **Step 5: Commit**

```bash
git add src/models/results.py tests/models/test_results.py
git commit -m "feat(models): RunFailure + PipelineResult loop-driver fields"
```

---

### Task 2: run_and_collect + error model + MetricsCollector wiring

**Files:**
- Modify: `src/harness/pipeline.py` (`__init__`: wire MetricsCollector; add `run_and_collect`)
- Test: `tests/harness/test_run_and_collect.py`

**Spec sections to read first:** "run_and_collect error model", and the spike `run_one_point` (`examples/steerability_spike.py`) as the reference pattern.

**Error model (from the spec — implement EXACTLY):**
- **Workload crash** (`proc.poll() is not None` during warmup, OR non-zero exit at `proc.wait`): NO retry (a crash is deterministic). -> skip this round, return `RunFailure(kind="crash", ...)`. Captures stdout/stderr/rc.
- **Timeout** (workload hangs -> `proc.wait` raises `TimeoutExpired`, OR `collect_topdown` returns a timeout error): retry up to `collect_retry` time(s). If still failing -> skip this round, return `RunFailure(kind="timeout", ...)`.
- **Collection failure** (devkit non-zero rc -> `CollectionResult.success is False`, OR the parsed topdown has no L1 lines -> format change): retry up to `collect_retry`. Persistent -> skip this round, return `RunFailure(kind="collect_fail", ...)`.
- A `RunFailure` is NOT a Profile; the loop driver (Task 3) handles streak counting. `run_and_collect` itself does NOT count streaks — it returns one attempt's outcome (with internal retry for timeout/collect_fail only, NOT for crashes).

- [ ] **Step 1: Write the failing tests**

Create `tests/harness/test_run_and_collect.py`. Use monkeypatched `subprocess.Popen` + `self.metrics_collector.collect_topdown` to inject each failure kind. A fake proc object with `.poll()`, `.pid`, `.wait(timeout)`, `.communicate()`, `.kill()`.

```python
"""Tests for Pipeline.run_and_collect (error model, monkeypatched)."""

import pathlib
import subprocess

import pytest

from harness.pipeline import Pipeline
from models.results import RunFailure
from profile.profile_schema import Profile, ProfileMetadata, TopdownL1


class _FakeProc:
    """Stand-in for a Popen process."""
    def __init__(self, pid: int, poll_rc: int | None, wait_rc: int | None = 0,
                 wait_raises_timeout: bool = False) -> None:
        self.pid = pid
        self._poll_rc = poll_rc
        self._wait_rc = wait_rc
        self._wait_raises = wait_raises_timeout
        self._killed = False

    def poll(self) -> int | None:
        return self._poll_rc

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        if self._wait_raises:
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout or 0)
        return self._wait_rc

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:  # noqa: ARG002
        return ("out", "err")

    def kill(self) -> None:
        self._killed = True


def _make_pipeline(monkeypatch: pytest.MonkeyPatch, collect_result) -> Pipeline:
    pipe = Pipeline(pathlib.Path("/tmp/x"))  # devkit_cmd None by default
    pipe.metrics_collector.collect_topdown = lambda *a, **k: collect_result  # type: ignore[method-assign]
    return pipe


def test_run_and_collect_crash_during_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _FakeProc(pid=999, poll_rc=139, wait_rc=139)  # poll!=None during warmup
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    pipe = _make_pipeline(monkeypatch, collect_result=None)
    rf = pipe.run_and_collect(
        binary_path="/bin/x", project_dir=pathlib.Path("/tmp/proj"),
        warmup_seconds=0, measurement_seconds=1,
    )
    assert isinstance(rf, RunFailure)
    assert rf.kind == "crash"


def test_run_and_collect_collect_failure_retries_then_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _FakeProc(pid=999, poll_rc=None, wait_rc=0)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    from models.results import CollectionResult
    pipe = _make_pipeline(
        monkeypatch, collect_result=CollectionResult(success=False, error="devkit rc=1")
    )
    rf = pipe.run_and_collect(
        binary_path="/bin/x", project_dir=pathlib.Path("/tmp/proj"),
        warmup_seconds=0, measurement_seconds=1,
    )
    assert isinstance(rf, RunFailure)
    assert rf.kind == "collect_fail"
    # collect_retry default is 1 -> 2 attempts total (1 + 1 retry).
    assert pipe.metrics_collector.collect_topdown.call_count >= 2  # type: ignore[attr-defined]


def test_run_and_collect_success_returns_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    proc = _FakeProc(pid=999, poll_rc=None, wait_rc=0)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    from models.results import CollectionResult
    td_text = "backend bound 72.0\nfrontend bound 10.0\nbad speculation 5.0\nretiring 13.0\n"
    pipe = _make_pipeline(
        monkeypatch,
        collect_result=CollectionResult(success=True, topdown_path=str(tmp_path / "td.txt")),
    )
    # collect_topdown writes the text file the parser reads.
    def _write_then_return(out_path, duration=1, interval=3, pid=None):  # noqa: ARG001
        pathlib.Path(out_path).write_text(td_text)
        return CollectionResult(success=True, topdown_path=str(out_path))
    pipe.metrics_collector.collect_topdown = _write_then_return  # type: ignore[method-assign]
    prof = pipe.run_and_collect(
        binary_path="/bin/x", project_dir=tmp_path,
        warmup_seconds=0, measurement_seconds=1,
    )
    assert isinstance(prof, Profile)
    assert prof.topdown is not None
    assert prof.topdown.backend_bound == 72.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/harness/test_run_and_collect.py -q`
Expected: FAIL (`Pipeline.run_and_collect` undefined).

- [ ] **Step 3: Write minimal implementation**

In `src/harness/pipeline.py`:

(a) Wire `MetricsCollector` in `__init__` (replace the no-arg construction):
```python
self.metrics_collector = MetricsCollector(devkit_cmd=self.config.devkit.devkit_cmd)
```

(b) Add the import of `write_config_json_atomic`? No — run_and_collect doesn't write; the loop does. Add `RunFailure` import: `from models.results import PipelineResult, RunFailure`. Add `import time` and `import subprocess` at top.

(c) Add `run_and_collect` method (mirrors the spike's `run_one_point` run/collect leg):
```python
def run_and_collect(
    self,
    binary_path: str,
    project_dir: pathlib.Path,
    warmup_seconds: int,
    measurement_seconds: int,
) -> Profile | RunFailure:
    """Run the existing binary, collect topdown during measurement, parse.

    Mirrors the spike's run_one_point run/collect leg: taskset-pin (if
    cpu_range configured), warm up, crash-check, collect_topdown(-p pid),
    wait for exit, parse. Returns a workload Profile or a RunFailure
    (crash/timeout/collect_fail). Crashes are NOT retried (deterministic);
    timeouts and collect-failures retry up to collect_retry.
    """
    config_path = str((project_dir / "config.json").resolve())
    binary = str(pathlib.Path(binary_path).resolve())
    cpu_range = self.config.devkit.cpu_range
    launch_cmd: list[str] = (
        ["taskset", "-c", cpu_range, binary, config_path]
        if cpu_range else [binary, config_path]
    )
    try:
        proc = subprocess.Popen(
            launch_cmd, cwd=str(project_dir),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except FileNotFoundError as exc:
        return RunFailure(reason=f"binary_launch_failed: {exc}", kind="crash")

    time.sleep(warmup_seconds)
    # Crash during warmup (e.g. a stage segfaulted): no retry.
    if proc.poll() is not None:
        try:
            out, err_out = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err_out = "<communicate-timeout>", "<communicate-timeout>"
        return RunFailure(
            reason=f"workload_exited_during_warmup rc={proc.returncode}",
            kind="crash", stdout=out or "", stderr=err_out or "",
        )

    interval = self.config.devkit.interval_seconds
    pid = proc.pid if self.config.devkit.collect_pid else None
    td_path = project_dir / "topdown.txt"
    retries = self.config.comparison.collect_retry
    last_err = ""
    for _ in range(retries + 1):
        coll = self.metrics_collector.collect_topdown(
            td_path, duration=measurement_seconds, interval=interval, pid=pid,
        )
        if coll.success and coll.topdown_path is not None:
            # Wait for the workload to exit (measurement + pad).
            try:
                proc.wait(timeout=measurement_seconds + _WORKLOAD_PAD_S + 30)
            except subprocess.TimeoutExpired:
                proc.kill()
                return RunFailure(reason="workload_hang", kind="timeout")
            try:
                prof = self.metrics_collector.parse_topdown_file(pathlib.Path(coll.topdown_path))
            except (ValueError, OSError) as exc:
                last_err = f"parse_failed: {exc}"
                continue
            if prof.topdown is None:
                last_err = "no_topdown_l1_lines"
                continue
            return prof
        last_err = coll.error or "collect_failed"
        # On a collect/timeout failure, retry (the workload may still be running;
        # loop retries collect_topdown). If the workload itself hung, the wait
        # below is reached only on a successful collect path.
    # Exhausted retries: ensure the process is reaped, then return a failure.
    try:
        proc.wait(timeout=_WORKLOAD_PAD_S + 30)
    except subprocess.TimeoutExpired:
        proc.kill()
    kind = "timeout" if "timeout" in last_err.lower() or "hang" in last_err.lower() else "collect_fail"
    return RunFailure(reason=last_err, kind=kind)
```

Add module-level constant near the top of `pipeline.py`:
```python
_WORKLOAD_PAD_S = 3  # spike's _WORKLOAD_MEASUREMENT_PAD_S; slack for proc.wait
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/harness/test_run_and_collect.py -q`
Expected: PASS (all tests). Note: the success test's fake proc has `wait_rc=0` so `proc.wait` returns 0 (no timeout).

- [ ] **Step 5: Commit**

```bash
git add src/harness/pipeline.py tests/harness/test_run_and_collect.py
git commit -m "feat(harness): run_and_collect + run/collect error model"
```

---

### Task 3: run_iteration_loop (two-tier driver) + stub-plant integration test

**Files:**
- Modify: `src/harness/pipeline.py` (add `run_iteration_loop`)
- Test: `tests/harness/test_run_iteration_loop.py`

**Spec sections to read first:** "Two-tier loop driver" (pseudocode), "Structural build-failure streak", "Oscillation detection", "Agent-unavailable degradation", "Termination", "observed_effects attribution rule". Implement the pseudocode faithfully.

**Seams (injected by the integration test; defaults wrap real components):**
- `collect(binary_path, instruction) -> Profile | RunFailure` (default: derives warmup/measurement from instruction["config"] / run_defaults, calls `self.run_and_collect(binary, project_dir, warmup, measurement)`).
- `build(instruction) -> str | None` (default: `self.generator.generate(instruction, project_dir)` then `self.build_runner.build(project_dir)`; returns `binary_path` or `None`).
- `sensitivity` dict (default `{}` — the real run loads from a path; tests pass a dict).

**Loop signature:**
```python
def run_iteration_loop(
    self,
    customer_profile: Profile,
    seed_instruction: dict[str, Any] | None = None,
    sensitivity: dict[str, dict[str, Any]] | None = None,
    max_iter: int = 10,
    collect: Callable[[str, dict[str, Any]], Profile | RunFailure] | None = None,
    build: Callable[[dict[str, Any]], str | None] | None = None,
) -> PipelineResult:
```

**Stub plant (the integration test):** a `collect` callable that returns a `Profile` whose `topdown` metrics move DETERMINISTICALLY toward the customer target as the runtime knobs (compute_ratio/memory_ratio) and structural knobs (working_set_mb/access_pattern/iterations) adjust — so the REAL `deterministic_revise` + REAL `validate_adjustments` + REAL `apply_adjustments` + REAL `ProfileComparator` drive convergence. A `build` callable that returns a fake binary path (and can be scripted to return `None` N times for the build-failure-streak test). A mock agent (`agent.is_available()` True/False, `agent.revise_instruction` returns a scripted revised instruction + adjustments). The sensitivity table is a dict the test provides matching the stub's knob->metric direction.

- [ ] **Step 1: Write the failing integration tests (one path at a time — TDD)**

Create `tests/harness/test_run_iteration_loop.py`. Build incrementally: write the converge-path test first, implement until green, then add each subsequent path. The full set of paths:

1. **converge**: stub metrics move toward target on each runtime adjustment; assert the loop returns `success=True, stop_reason="converged"` within `max_iter`, `best_iteration` set.
2. **max_iter / best_iteration by score**: a stub that improves then plateaus without converging; assert `stop_reason="max_iter"` (or `"no_improvement_stop"`), `best_iteration` is the argmin-score iteration (not the last).
3. **escalate to LLM tier**: seed a structural gap (priority>=2); mock agent available + `revise_instruction` returns a structural adjustment; assert the structural path is taken (build called with the revised instruction) and the gate accepts a good-direction structural adjustment.
4. **oscillation stop**: script the stub so a runtime knob reverses direction within `oscillation_window`; assert `stop_reason` reflects oscillation, loop terminates early.
5. **no_improvement stop**: script the stub so `no_improvement_stop` consecutive iterations don't refresh best score; assert early termination.
6. **run-failure streak**: `collect` returns `RunFailure(kind="crash")` for `run_failure_stop` consecutive rounds; assert `stop_reason` reflects run-failure streak.
7. **build-failure streak**: `build` returns `None` for `build_failure_stop` consecutive structural revisions; assert `build_failed` records + early termination with build-failure stop reason; the next `revise_instruction` would have received the stderr (verify the record carries `build_stderr`).
8. **degraded mode**: mock agent `is_available()` False; seed a structural gap (priority>=2); assert the loop does NOT terminate on the structural priority alone, continues runtime-tier-only (`degraded=True`), and stops only on a runtime-tier stall (oscillation/no_improvement/all-runtime-knobs-skip-blocked) with `stop_reason="runtime_tier_exhausted_agent_unavailable"`.

**Stub plant design (de-risked by reading the comparator — verify at implement time):**
- `ProfileComparator._compare_hotspot_coverage`: if the CUSTOMER profile has no open-source hotspots, `coverage_pct = 100.0` (coverage always OK). `_compare_memory` only diffs `bandwidth_gbps` when BOTH profiles have it; if either is None, memory is OK.
- So the stub plant's customer_profile AND workload Profile can use `hotspots=[]` + `memory=None` -> coverage=100%, memory=OK, convergence driven PURELY by topdown. The stub's `collect(binary, instruction)` returns `Profile(metadata=ProfileMetadata(customer=...), topdown=TopdownL1(... values that move toward the customer target as the instruction's knobs move in their proven direction ...))`.
- Tier escalation is natural: a large topdown error -> `decide_iteration_priority` returns 4/3/2 (structural); as knobs move and the error shrinks below 5.0 -> priority 1 (runtime); when all within threshold -> priority 0 (converged). So a single well-designed stub exercises BOTH tiers on the way to convergence (no need to force a tier).

(Test code is substantial; the implementer writes it path-by-path. The controller provides the seams above + the spec pseudocode; the implementer designs the stub's metric-movement function so the REAL deterministic controller converges.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/harness/test_run_iteration_loop.py -q`
Expected: FAIL (`run_iteration_loop` undefined).

- [ ] **Step 3: Write minimal implementation**

Implement `Pipeline.run_iteration_loop` following the spec's "Two-tier loop driver" pseudocode + the error/streak/degradation/termination sections. Key structure (condensed from the spec — read the spec for the verbatim rules):

```python
def run_iteration_loop(self, customer_profile, seed_instruction=None,
                       sensitivity=None, max_iter=10, collect=None, build=None):
    if collect is None:
        collect = lambda binary, instr: self.run_and_collect(
            binary, project_dir,
            instr["config"].get("warmup_seconds", self.config.run_defaults.warmup_seconds),
            instr["config"].get("measurement_seconds", self.config.run_defaults.measurement_seconds),
        )
    if build is None:
        build = self._build_instruction  # generate + BuildRunner.build -> binary|None
    sens = sensitivity if sensitivity is not None else {}
    history = IterationHistory(customer_name=customer_profile.metadata.customer)
    # initial instruction: agent.run_full_chain if available+seed None else seed
    instruction = seed_instruction if seed_instruction is not None else (
        self.agent.run_full_chain(customer_profile.model_dump_json())
        if self.agent.is_available() else seed_instruction
    )
    if instruction is None:
        return PipelineResult(success=False, error="no seed instruction and agent unavailable", stop_reason="no_instruction")
    project_dir = self.output_base_dir / "iteration_loop"
    binary = build(instruction)
    if binary is None:
        return PipelineResult(success=False, error="seed build failed", stop_reason="seed_build_failed",
                              history=...)  # seed must compile; no recovery
    run_fail_streak = build_fail_streak = 0
    last_report = None
    pending_build_fix = False
    for i in range(max_iter):
        if pending_build_fix:
            report = last_report
            pending_build_fix = False
        else:
            outcome = collect(binary, instruction)
            if isinstance(outcome, RunFailure):
                run_fail_streak += 1
                history.add_record(IterationRecord(iteration=i, converged=False,
                    failed=True, failure_reason=outcome.reason))
                if run_fail_streak >= self.config.comparison.run_failure_stop:
                    return self._loop_result(history, "run_failure_streak")
                continue
            run_fail_streak = 0
            report = self.comparator.compare(customer_profile, outcome, i)
            last_report = report
        priority = decide_iteration_priority(report, self.config.comparison)
        # build the record (adjustments/applied_moves/observed_effects filled after apply)
        accepted, rejected = [], []
        tier = "runtime" if priority == 1 else "structural"
        if priority == 0:
            history.add_record(self._make_record(i, report, priority, []))
            return self._loop_result(history, "converged")
        # candidate adjustments
        if tier == "runtime":
            cand = deterministic_revise(instruction, report, sens, history)
        else:
            if self.agent.is_available():
                # Per spec pseudocode: use the LLM's emitted ADJUSTMENTS ([1]),
                # NOT its revised_instruction ([0]). The gate +
                # apply_adjustments steer via the structured knob contract; the
                # revised_instruction is the LLM's rendering of the same changes
                # (provenance / future free-form-revision mode). The gate
                # re-derives the effective move (actual_current -> to) against
                # the live (pre-apply) instruction.
                _revised, cand = self.agent.revise_instruction(instruction, report, sens, history)
            else:
                history.degraded = True
                cand = deterministic_revise(instruction, report, sens, history)
        accepted, rejected = validate_adjustments(cand, instruction, report, sens, tier)
        if rejected:
            logger.info("adjustments_rejected", count=len(rejected))
        applied_moves = []
        if accepted:
            apply_adjustments(instruction, accepted)
            applied_moves = self._applied_moves(accepted, instruction, tier)  # {knob,tier,sign}
            if tier == "runtime":
                write_config_json_atomic(project_dir / "config.json", instruction["config"])
            else:
                new_binary = build(instruction)
                if new_binary is None:
                    build_fail_streak += 1
                    history.add_record(IterationRecord(iteration=i, converged=False,
                        build_failed=True, build_stderr="(build returned None)"))
                    pending_build_fix = True
                    if build_fail_streak >= self.config.comparison.build_failure_stop:
                        return self._loop_result(history, "build_failure_streak")
                    continue
                binary = new_binary
                build_fail_streak = 0
        rec = self._make_record(i, report, priority, accepted, applied_moves)
        history.add_record(rec)
        if history.is_oscillating(self.config.comparison.oscillation_window) \
           or history.no_improvement_for(self.config.comparison.no_improvement_stop):
            reason = "oscillation" if history.is_oscillating(self.config.comparison.oscillation_window) else "no_improvement_stop"
            if self.agent.is_available() is False and self._runtime_exhausted(sens, instruction, history):
                return self._loop_result(history, "runtime_tier_exhausted_agent_unavailable")
            return self._loop_result(history, reason)
    return self._loop_result(history, "max_iter")
```

Helper methods on Pipeline (implement alongside): `_build_instruction(instruction)` (generate+build->binary|None), `_make_record(i, report, priority, adjustments, applied_moves=[])` (builds an IterationRecord with topdown_diffs/memory/coverage/score/adjustments/applied_moves), `_applied_moves(accepted, instruction, tier)` (per accepted adjustment, `{knob, tier, sign=sign(to-actual)}`), `_loop_result(history, stop_reason)` (returns PipelineResult with best_iteration/success/degraded/stop_reason/history_path — saves history to disk), `_runtime_exhausted(sens, instruction, history)` (True when all runtime knobs are skip-blocked or no-improvement while degraded).

**observed_effects attribution (spec rule):** single-adjustment round -> per-knob `{expected_metric: next_report[metric]-this_report[metric]}`; multi-adjustment (LLM batch) round -> overall deltas only (no per-knob). The `_make_record` for the NEXT iteration computes observed_effects for the PREVIOUS record from this round's report deltas. (Implement carefully — the spec's attribution rule is the subtlest part.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/harness/test_run_iteration_loop.py -q`
Expected: PASS (all 8 paths). Iterate: implement until each path's test is green (TDD — write a path test, implement to green, add the next).

- [ ] **Step 5: Commit**

```bash
git add src/harness/pipeline.py tests/harness/test_run_iteration_loop.py
git commit -m "feat(harness): run_iteration_loop two-tier driver + stub-plant integration test"
```

---

### Task 4: Full quality gate + open the PR

**Files:** none (verification + PR).

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: all pass, coverage >= 60%. Note the count + coverage for the PR body.

- [ ] **Step 2: Run pre-commit on all files**

Run: `python -m pre_commit run --all-files`
Expected: all hooks pass (ruff, ruff-format, mypy strict src/+tests/, trailing-ws, eof, yaml, json, no-large-files, don't-commit-to-branch). mypy strict on `src/harness/pipeline.py` + `src/models/results.py` — the loop has `Callable` params and `Profile | RunFailure` unions; annotate carefully. Targeted `# type: ignore` only if a genuine pydantic/subprocess limitation blocks mypy (justify in a comment).

- [ ] **Step 3: Push + open the PR**

```bash
git push -u origin feat/phase2-pr3b-loop-driver
gh pr create --base main --title "Phase 2 PR 3b: loop driver + run/collect + error model + degradation" --body '<PR_BODY>'
```

PR body (no Claude trailer):
```
Wires the Phase 2 auto-iteration loop per the approved spec. Depends on PR 3a (#NN, merged: DevkitConfig + nlohmann-free config_loader + write_config_json_atomic) + PR 1 (#51) + PR 2 (#52).

## What
- src/models/results.py: RunFailure (crash/timeout/collect_fail); PipelineResult gains best_iteration, degraded, stop_reason, history_path.
- src/harness/pipeline.py: MetricsCollector wired with devkit_cmd (closes #47); run_and_collect (taskset Popen + warmup + crash-check + collect_topdown(-p pid) + wait + parse -> Profile | RunFailure) with the error model (crash=no-retry; timeout/collect_fail=retry collect_retry then skip); run_iteration_loop (two-tier: runtime no-rebuild via write_config_json_atomic / structural regenerate+rebuild; validate_adjustments gate; deterministic_revise (runtime) vs agent.revise_instruction (structural); build-failure streak; run-failure streak; agent-unavailable degradation; oscillation/no-improvement/max_iter termination) -> PipelineResult(best_iteration by score, degraded, stop_reason).
- tests/harness/test_run_and_collect.py: error-model unit tests (monkeypatched Popen + collect_topdown).
- tests/harness/test_run_iteration_loop.py: stub-plant integration test (REAL deterministic controller + REAL gate + REAL comparator; stubbed plant/agent): converge, max_iter/best_iteration, escalate-to-LLM, oscillation-stop, no-improvement-stop, run-failure-streak, build-failure-streak, degraded-mode (runtime-only continues through priority>=2, stops on runtime-tier stall with degraded=True).

## Scope
Real ARM run is user-side (per spec). No RFC 0001/0002, no units renormalization (#46).

## Verification
- python -m pytest tests/ -q -> <N> passed, <cov>% (gate 60%)
- python -m pre_commit run --all-files -> all hooks pass
```

- [ ] **Step 4: Update memory**

Update `mirage-project-state.md`: PR 3b opened (#NN). Per the standing "review -> merge -> continue" delegation: controller does a final holistic self-review of 3b (the loop has subtle invariants — verify each of the 8 integration paths against the spec); if clean, squash-merge 3b to main, then Phase 2 PR 3 is complete (loop lands). Surface the real-ARM run as the remaining user-side step.

---

## Self-Review (controller runs before opening the PR)

1. **Spec coverage:** "Two-tier loop driver" -> Task 3. "run_and_collect error model" -> Task 2. "Structural build-failure streak" -> Task 3. "Oscillation detection" -> uses PR 1 `is_oscillating` (verify the loop feeds `applied_moves`). "Agent-unavailable degradation" -> Task 3 degraded path. "Termination" -> Task 3 all reasons. "observed_effects attribution" -> Task 3 `_make_record`. "Atomic config.json writes" -> uses PR 3a `write_config_json_atomic`. "DevkitConfig (#47)" -> Task 2 wiring. All spec sections covered.
2. **Placeholder scan:** the Task 3 implementation block is condensed pseudocode (the spec is the source of truth); the implementer MUST read the spec sections. No TBD/vague in the seams/signatures/tests.
3. **Type consistency:** `RunFailure(reason, kind, stdout, stderr)` matches test. `PipelineResult` new fields match test. `run_and_collect(binary, project_dir, warmup, measurement) -> Profile | RunFailure`. `run_iteration_loop(customer_profile, seed_instruction, sensitivity, max_iter, collect, build) -> PipelineResult`. `collect(binary, instruction)`, `build(instruction) -> str|None`. `applied_moves` items `{knob, tier, sign}` match PR 1's `is_oscillating` reader.
4. **ASCII-only:** all code above is ASCII; implementer verifies with a final check.
