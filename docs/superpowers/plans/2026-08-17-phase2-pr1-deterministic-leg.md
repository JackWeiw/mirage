# Phase 2 PR 1 — Deterministic Leg + Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the pure, no-LLM, no-loop-driver half of the Phase 2 auto-iteration loop: the adjustment mechanics, sensitivity loader, deterministic runtime controller, the validation gate, the normalized score, and the history extensions that the loop driver (PR 3) will drive.

**Architecture:** A new `src/agent/adjustment.py` holds the pure control functions (knob space, apply/validate/revise/load). `src/observability/iteration_history.py` is extended with `score`, `applied_moves`, `observed_effects`, `is_oscillating` (precise per-knob signed-move rule), `no_improvement_for` (best-refresh semantics), and best-by-score excluding failed records. `src/config/framework_config.py` gains the loop-control knobs on `ComparisonConfig`. Everything is unit-tested with fakes — no ARM/devkit/LLM.

**Tech Stack:** Python 3.13, pydantic v2, pytest, ruff + ruff-format, mypy strict (src/). `pythonpath=["src"]` so imports are bare (`from agent.adjustment import ...`). Coverage gate ≥ 60%.

**Spec:** `docs/superpowers/specs/2026-08-17-phase2-auto-iteration-loop-design.md` (approved).

**Branch:** `feat/phase2-deterministic-leg` off `main`. All commits English, relevant-only, **no** `Co-Authored-By` line. Squash-merge convention.

---

## Key facts the implementer must know (verified against the code)

- **Report shape** (`src/profile/comparator.py`): `report["topdown_l1"][metric]` has keys `{"customer","workload","diff","diff_pct","within_threshold"}` for metric ∈ `{"frontend_bound","backend_bound","bad_speculation","retiring"}`. `report["memory"]["bandwidth_gbps"]["diff_pct"]`. `report["hotspot_coverage"]["coverage_pct"]`. `report["convergence"]["converged"]`.
- **diff sign:** `diff_pct = (workload − customer) / customer * 100`. So **positive `diff_pct` = the workload metric is TOO HIGH** vs customer; **negative = too low**. This is the sign the gate's direction check uses.
- **Sensitivity `sensitivity.json`** (produced by `examples/steerability_spike.py`): `{"rows": [...], "verdicts": [...]}`. Each verdict is `{"knob", "target_metric", "expected" (<- the direction, "up"/"down"), "verdict" ("controllable"/"weak"/"dead"), "values": [...], "metric_values": [...]}`. A dead knob verdict has `{"knob","verdict":"dead","reason"}` (no `expected`/`target_metric`). **Note the field is `expected`, not `expected_direction`** — `load_sensitivity` renames it.
- **Knob → stage location:** structural knobs live at `instruction["stages"][i]["strategies"][0]["synthesis_config"][knob]`, matched by `stages[i]["stage_name"] == adjustment["stage"]`. Runtime knobs live at `instruction["config"][knob]`.
- **Existing `IterationHistory.add_record`** uses a raw `sum(abs(topdown_diffs.values()))` heuristic — this plan replaces it with the normalized `compute_score`. Existing tests (see `tests/observability/test_iteration_history.py`) must stay green; the new score is monotonic in `|diff|` so their rankings are preserved.
- **`decide_iteration_priority`** (`src/agent/strategy.py`) stays untouched. PR 1 does not call it; the loop driver (PR 3) does.

## Deviations from the spec (documented, justified)

1. **`compute_score` lives in `iteration_history.py`, not `adjustment.py`.** The spec's PR-1 list groups `score` with `adjustment.py`, but `iteration_history.py` must call it in `add_record`; putting it in `observability` avoids `observability` importing `agent` (wrong layering direction). It is still a pure function. `adjustment.py` does not need it.
2. **`deterministic_revise` signature is `(instruction, report, sensitivity, history)`**, not `(report, sensitivity, history)` — already patched into the spec (`0cda0fd`). The controller must read actual current values to set `from` and produce absolute `to`.
3. **`IterationRecord` gains an `applied_moves: list[dict]` field** (each `{"knob": str, "tier": "runtime"|"structural", "sign": int}`) in addition to `adjustments`. The spec's oscillation algorithm operates on "the signed direction of each applied move"; `applied_moves` is that log. `adjustments` stays as the raw emitted list for inspectability. The loop driver (PR 3) populates `applied_moves`; PR 1 tests construct it directly.

## File structure

- **Create** `src/agent/adjustment.py` — knob space + domains, `apply_adjustments`, `apply_adjustments_to_config`, `load_sensitivity`, `validate_adjustments`, `deterministic_revise`.
- **Modify** `src/config/framework_config.py` — add loop-control knobs to `ComparisonConfig`.
- **Modify** `src/observability/iteration_history.py` — extend `IterationRecord` + `IterationHistory`; add `compute_score`.
- **Create** `tests/agent/test_adjustment.py` — all `adjustment.py` tests.
- **Modify** `tests/observability/test_iteration_history.py` — add new-behavior tests; keep existing ones green.
- **Create** `tests/data/sensitivity_sample.json` — minimal fixture matching the spike's output shape.

---

### Task 1: ComparisonConfig loop-control knobs

**Files:**
- Modify: `src/config/framework_config.py:15-18`
- Test: `tests/config/test_framework_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/config/test_framework_config.py`:

```python
from config.framework_config import ComparisonConfig


def test_comparison_config_loop_control_defaults() -> None:
    c = ComparisonConfig()
    assert c.oscillation_window == 3
    assert c.no_improvement_stop == 3
    assert c.run_failure_stop == 2
    assert c.build_failure_stop == 2
    assert c.collect_retry == 1
    # Existing thresholds unchanged.
    assert c.topdown_threshold_pct == 10.0
    assert c.memory_threshold_pct == 5.0
    assert c.coverage_threshold_pct == 80.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/config/test_framework_config.py::test_comparison_config_loop_control_defaults -q`
Expected: FAIL with `ValidationError` / `AttributeError: 'ComparisonConfig' object has no attribute 'oscillation_window'`.

- [ ] **Step 3: Implement**

Replace the `ComparisonConfig` class body in `src/config/framework_config.py`:

```python
class ComparisonConfig(BaseModel):
    topdown_threshold_pct: float = 10.0
    memory_threshold_pct: float = 5.0
    coverage_threshold_pct: float = 80.0
    oscillation_window: int = 3  # iters looked back for knob direction reversals
    no_improvement_stop: int = 3  # K consecutive iters w/o a new best score -> stop
    run_failure_stop: int = 2  # consecutive run/collect failures -> stop
    build_failure_stop: int = 2  # consecutive structural build failures -> stop
    collect_retry: int = 1  # retries on a transient collect/timeout
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/config/test_framework_config.py::test_comparison_config_loop_control_defaults -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/config/framework_config.py tests/config/test_framework_config.py
git commit -m "feat(config): add loop-control knobs to ComparisonConfig"
```

---

### Task 2: `compute_score` + `IterationRecord`/`IterationHistory` field extensions

**Files:**
- Modify: `src/observability/iteration_history.py`
- Test: `tests/observability/test_iteration_history.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/observability/test_iteration_history.py`:

```python
from observability.iteration_history import IterationHistory, IterationRecord, compute_score


def test_compute_score_normalized_and_lower_is_better() -> None:
    # topdown diffs 12%+5% with default threshold 10 -> 1.2+0.5; coverage 70 -> (80-70)/80=0.125
    r = IterationRecord(
        iteration=1,
        converged=False,
        topdown_diffs={"backend_bound": 12.0, "frontend_bound": 5.0},
        memory_diff_pct=4.0,
        coverage_pct=70.0,
    )
    # 12/10 + 5/10 + 4/5 + max(0,80-70)/80 = 1.2+0.5+0.8+0.125 = 2.625
    assert compute_score(r) == 2.625


def test_compute_score_converged_scores_near_zero() -> None:
    r = IterationRecord(
        iteration=1,
        converged=True,
        topdown_diffs={"backend_bound": 1.0},  # within threshold
        memory_diff_pct=0.5,
        coverage_pct=85.0,  # above threshold
    )
    # 1/10 + 0.5/5 + max(0,80-85)/80(=0) = 0.1+0.1+0 = 0.2
    assert compute_score(r) == 0.2


def test_record_carries_new_fields() -> None:
    r = IterationRecord(
        iteration=1,
        converged=False,
        topdown_diffs={"backend_bound": 10.0},
        adjustments=[{"knob": "compute_ratio", "to": 0.8}],
        applied_moves=[{"knob": "compute_ratio", "tier": "runtime", "sign": 1}],
        observed_effects={"retiring": 3.0},
    )
    assert r.adjustments[0]["knob"] == "compute_ratio"
    assert r.applied_moves[0]["sign"] == 1
    assert r.observed_effects["retiring"] == 3.0


def test_add_record_computes_score_when_absent_and_picks_best() -> None:
    history = IterationHistory(customer_name="t")
    worse = IterationRecord(iteration=1, converged=False, topdown_diffs={"b": 20.0})
    better = IterationRecord(iteration=2, converged=False, topdown_diffs={"b": 4.0})
    history.add_record(worse)
    history.add_record(better)
    assert better.score is not None
    assert worse.score is not None
    assert better.score < worse.score
    assert history.best_iteration == 2


def test_failed_records_excluded_from_best_iteration() -> None:
    history = IterationHistory(customer_name="t")
    good = IterationRecord(iteration=1, converged=False, topdown_diffs={"b": 4.0})
    crash = IterationRecord(iteration=2, converged=False, topdown_diffs={}, failed=True)
    history.add_record(good)
    history.add_record(crash)
    assert history.best_iteration == 1  # crash excluded; best stays the good one
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/observability/test_iteration_history.py -q`
Expected: FAIL — `compute_score` not importable, new fields rejected by pydantic.

- [ ] **Step 3: Implement**

Replace the whole contents of `src/observability/iteration_history.py` with:

```python
"""Track iteration results and convergence trends across multiple iterations."""

import json
import pathlib

from config.framework_config import ComparisonConfig
from pydantic import BaseModel, Field, PrivateAttr


def compute_score(
    record: "IterationRecord", comparison: ComparisonConfig | None = None
) -> float:
    """Normalized multi-dim score; lower is better.

    score = sum(|topdown_diff| / topdown_threshold)
          + |memory_diff| / memory_threshold
          + max(0, coverage_threshold - coverage) / coverage_threshold

    A converged iteration scores ~0 on every exceeded-threshold term.
    """
    if comparison is None:
        comparison = ComparisonConfig()

    topdown_term = sum(
        abs(v) for v in record.topdown_diffs.values()
    ) / comparison.topdown_threshold_pct
    memory_term = abs(record.memory_diff_pct) / comparison.memory_threshold_pct
    coverage_term = max(
        0.0, comparison.coverage_threshold_pct - record.coverage_pct
    ) / comparison.coverage_threshold_pct
    return topdown_term + memory_term + coverage_term


class IterationRecord(BaseModel):
    """Record of one iteration's comparison results."""

    iteration: int
    converged: bool
    topdown_diffs: dict[str, float] = Field(default_factory=dict)
    memory_diff_pct: float = 0.0
    coverage_pct: float = 0.0
    strategy_priority: int = 0
    duration_seconds: float = 0.0
    timestamp: str = ""
    # --- Phase 2 extensions ---
    score: float | None = None
    adjustments: list[dict[str, object]] = Field(default_factory=list)  # raw emitted adjustments
    applied_moves: list[dict[str, object]] = Field(default_factory=list)  # {knob, tier, sign}
    observed_effects: dict[str, float] = Field(default_factory=dict)
    failed: bool = False  # run/collect failure
    build_failed: bool = False
    failure_reason: str = ""
    build_stderr: str = ""


class IterationHistory(BaseModel):
    """History of all iterations for a workload simulation run."""

    customer_name: str
    records: list[IterationRecord] = Field(default_factory=list)
    best_iteration: int | None = None
    total_iterations: int = 0
    degraded: bool = False
    _best_index: int = PrivateAttr(default=0)

    def add_record(self, record: IterationRecord) -> None:
        """Add an iteration record and update best_iteration by score.

        Failed / build-failed records are excluded from best_iteration (they
        have no measured score). The score is computed from the record's
        topdown/memory/coverage if the caller did not supply one.
        """
        if record.score is None:
            record.score = compute_score(record)
        self.records.append(record)
        self.total_iterations = len(self.records)

        if record.failed or record.build_failed:
            return  # infra failure: never becomes best_iteration

        new_index = len(self.records) - 1
        if self.best_iteration is None:
            self.best_iteration = record.iteration
            self._best_index = new_index
            return
        current_best = self.records[self._best_index]
        if current_best.failed or current_best.build_failed or (
            record.score < (current_best.score if current_best.score is not None else float("inf"))
        ):
            self.best_iteration = record.iteration
            self._best_index = new_index

    def get_convergence_trend(self) -> list[dict[str, float]]:
        """Get convergence trend: Topdown diffs over iterations."""
        return [
            {"iteration": r.iteration, "total_diff": sum(abs(v) for v in r.topdown_diffs.values())}
            for r in self.records
        ]

    def is_converging(self) -> bool:
        """Check if the trend is improving (diffs getting smaller)."""
        if len(self.records) < 2:
            return True
        trend = self.get_convergence_trend()
        recent = trend[-3:]
        return all(
            recent[i]["total_diff"] >= recent[i + 1]["total_diff"] for i in range(len(recent) - 1)
        )

    def recent_adjustments(self, n: int) -> list[dict[str, object]]:
        """Flat list of raw adjustments from the last n records."""
        out: list[dict[str, object]] = []
        for r in self.records[-n:]:
            out.extend(r.adjustments)
        return out

    def save(self, filepath: pathlib.Path) -> pathlib.Path:
        """Save iteration history to JSON file."""
        filepath.write_text(self.model_dump_json(indent=2))
        return filepath

    @classmethod
    def load(cls, filepath: pathlib.Path) -> "IterationHistory":
        """Load iteration history from JSON file."""
        data = json.loads(filepath.read_text())
        return cls.model_validate(data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/observability/test_iteration_history.py -q`
Expected: PASS (all new + the pre-existing 5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/observability/iteration_history.py tests/observability/test_iteration_history.py
git commit -m "feat(history): normalized score, applied_moves, observed_effects; best excludes failed"
```

---

### Task 3: `no_improvement_for` (best-refresh semantics)

**Files:**
- Modify: `src/observability/iteration_history.py` (add method to `IterationHistory`)
- Test: `tests/observability/test_iteration_history.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_no_improvement_for_counts_non_best_refreshes() -> None:
    history = IterationHistory(customer_name="t")
    # best=10 (score 1.0); then 3 non-improving runs -> stop
    history.add_record(IterationRecord(iteration=1, converged=False, topdown_diffs={"b": 10.0}))
    history.add_record(IterationRecord(iteration=2, converged=False, topdown_diffs={"b": 12.0}))
    history.add_record(IterationRecord(iteration=3, converged=False, topdown_diffs={"b": 11.0}))
    history.add_record(IterationRecord(iteration=4, converged=False, topdown_diffs={"b": 13.0}))
    assert history.no_improvement_for(3) is True


def test_no_improvement_resets_on_new_best() -> None:
    history = IterationHistory(customer_name="t")
    history.add_record(IterationRecord(iteration=1, converged=False, topdown_diffs={"b": 10.0}))
    history.add_record(IterationRecord(iteration=2, converged=False, topdown_diffs={"b": 12.0}))
    history.add_record(IterationRecord(iteration=3, converged=False, topdown_diffs={"b": 5.0}))  # new best
    assert history.no_improvement_for(3) is False


def test_no_improvement_ignores_failed_rounds() -> None:
    history = IterationHistory(customer_name="t")
    history.add_record(IterationRecord(iteration=1, converged=False, topdown_diffs={"b": 10.0}))
    # 2 run-failed rounds: must NOT count as no-improvement (infra, not control)
    history.add_record(IterationRecord(iteration=2, converged=False, topdown_diffs={}, failed=True))
    history.add_record(IterationRecord(iteration=3, converged=False, topdown_diffs={}, failed=True))
    assert history.no_improvement_for(2) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/observability/test_iteration_history.py -k no_improvement -q`
Expected: FAIL — `AttributeError: 'IterationHistory' object has no attribute 'no_improvement_for'`.

- [ ] **Step 3: Implement**

Add this method to the `IterationHistory` class (after `recent_adjustments`):

```python
    def no_improvement_for(self, k: int) -> bool:
        """True if the last k *non-failed* iterations failed to set a new best score.

        Failed / build-failed rounds are skipped entirely (infra failure, no
        score) — they neither advance nor reset the streak. A round that
        refreshes the running minimum resets the streak to 0.
        """
        streak = 0
        best = float("inf")
        for r in self.records:
            if r.failed or r.build_failed:
                continue  # invisible to this streak
            score = r.score if r.score is not None else compute_score(r)
            if score < best:
                best = score
                streak = 0
            else:
                streak += 1
                if streak >= k:
                    return True
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/observability/test_iteration_history.py -k no_improvement -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/observability/iteration_history.py tests/observability/test_iteration_history.py
git commit -m "feat(history): no_improvement_for with best-refresh semantics"
```

---

### Task 4: `is_oscillating` (precise per-knob signed-move rule)

**Files:**
- Modify: `src/observability/iteration_history.py` (add method to `IterationHistory`)
- Test: `tests/observability/test_iteration_history.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
def _move_rec(i: int, knob: str, tier: str, sign: int) -> IterationRecord:
    return IterationRecord(
        iteration=i,
        converged=False,
        topdown_diffs={"b": 10.0},
        applied_moves=[{"knob": knob, "tier": tier, "sign": sign}],
    )


def test_oscillating_runtime_one_reversal_fires() -> None:
    history = IterationHistory(customer_name="t")
    history.add_record(_move_rec(1, "compute_ratio", "runtime", 1))
    history.add_record(_move_rec(2, "compute_ratio", "runtime", -1))  # reversal
    assert history.is_oscillating(3) is True


def test_oscillating_structural_one_reversal_does_not_fire() -> None:
    history = IterationHistory(customer_name="t")
    history.add_record(_move_rec(1, "archetype", "structural", 1))
    history.add_record(_move_rec(2, "archetype", "structural", -1))  # one reversal only
    assert history.is_oscillating(3) is False


def test_oscillating_structural_full_pingpong_fires() -> None:
    history = IterationHistory(customer_name="t")
    history.add_record(_move_rec(1, "archetype", "structural", 1))
    history.add_record(_move_rec(2, "archetype", "structural", -1))
    history.add_record(_move_rec(3, "archetype", "structural", 1))  # + - + pingpong, no improve
    assert history.is_oscillating(3) is True


def test_oscillating_same_direction_is_continuation() -> None:
    history = IterationHistory(customer_name="t")
    history.add_record(_move_rec(1, "compute_ratio", "runtime", 1))
    history.add_record(_move_rec(2, "compute_ratio", "runtime", 1))  # same dir, not reversal
    assert history.is_oscillating(3) is False


def test_oscillating_any_single_knob_fires() -> None:
    history = IterationHistory(customer_name="t")
    history.add_record(_move_rec(1, "compute_ratio", "runtime", 1))
    history.add_record(_move_rec(2, "memory_ratio", "runtime", -1))  # different knob, no reversal per-knob
    assert history.is_oscillating(3) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/observability/test_iteration_history.py -k oscillating -q`
Expected: FAIL — `AttributeError: ... no attribute 'is_oscillating'`.

- [ ] **Step 3: Implement**

Add this method to the `IterationHistory` class (after `no_improvement_for`):

```python
    def is_oscillating(self, window: int) -> bool:
        """Precise oscillation rule (see spec 'Oscillation detection').

        Per knob, collect the signed moves (from applied_moves) within the
        last `window` records. A *reversal* is two consecutive moves on the
        same knob with opposite signs.

        - Runtime knob: one reversal in the window -> oscillation.
        - Structural knob: a full ping-pong (two reversals: + - + or - + -)
          AND the window did not improve the best score -> oscillation.

        A no-op record (no applied_moves) is invisible. Same-direction repeats
        are continuation, not a reversal. Any single knob tripping its rule
        fires the stop.
        """
        recent = self.records[-window:] if window > 0 else self.records
        # Best score before this window (for the structural no-improve test).
        prior = self.records[: max(0, len(self.records) - window)]
        best_before = min(
            (r.score for r in prior if not r.failed and not r.build_failed and r.score is not None),
            default=float("inf"),
        )
        window_best = min(
            (r.score for r in recent if not r.failed and not r.build_failed and r.score is not None),
            default=float("inf"),
        )
        improved_in_window = window_best < best_before

        moves_by_knob: dict[str, list[tuple[str, int]]] = {}
        for r in recent:
            for mv in r.applied_moves:
                knob = str(mv["knob"])
                tier = str(mv["tier"])
                sign = int(mv["sign"])
                if sign == 0:
                    continue
                moves_by_knob.setdefault(knob, []).append((tier, sign))

        def _reversals(seq: list[int]) -> int:
            count = 0
            for a, b in zip(seq, seq[1:]):
                if a * b < 0:  # opposite signs
                    count += 1
            return count

        for knob, seq in moves_by_knob.items():
            signs = [s for (_t, s) in seq]
            tiers = {t for (t, _s) in seq}
            reversals = _reversals(signs)
            if "runtime" in tiers and reversals >= 1:
                return True
            if "structural" in tiers and reversals >= 2 and not improved_in_window:
                return True
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/observability/test_iteration_history.py -k oscillating -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/observability/iteration_history.py tests/observability/test_iteration_history.py
git commit -m "feat(history): is_oscillating precise per-knob signed-move rule"
```

---

### Task 5: Knob space + domains + `apply_adjustments`

**Files:**
- Create: `src/agent/adjustment.py` (start of file — knob space + apply)
- Test: `tests/agent/test_adjustment.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/agent/test_adjustment.py`:

```python
"""Tests for adjustment application (pure, no LLM/devkit)."""

import pytest

from agent.adjustment import apply_adjustments, KNOB_DOMAINS


def _instr() -> dict:
    return {
        "project_name": "sim",
        "stages": [
            {
                "stage_name": "mem_stage",
                "implementation_strategy": "memory_synthesis",
                "strategies": [{"strategy": "memory_synthesis",
                                "synthesis_config": {"working_set_mb": 64, "access_pattern": "random"}}],
            },
            {
                "stage_name": "comp_stage",
                "implementation_strategy": "compute_synthesis",
                "strategies": [{"strategy": "compute_synthesis",
                                "synthesis_config": {"archetype": "matmul", "iterations": 100}}],
            },
        ],
        "config": {"compute_ratio": 0.5, "memory_ratio": 0.5, "thread_count": 4, "qps": 100},
    }


def test_apply_structural_knob_routes_to_synthesis_config() -> None:
    out = apply_adjustments(_instr(), [
        {"stage": "mem_stage", "knob": "working_set_mb", "from": 64, "to": 256,
         "rationale": "", "expected_metric": "backend_bound", "expected_direction": "up"}
    ])
    assert out["stages"][0]["strategies"][0]["synthesis_config"]["working_set_mb"] == 256


def test_apply_structural_archetype_enum() -> None:
    out = apply_adjustments(_instr(), [
        {"stage": "comp_stage", "knob": "archetype", "from": "matmul", "to": "hash",
         "rationale": "", "expected_metric": "retiring", "expected_direction": "up"}
    ])
    # stages[0] (mem_stage) is untouched — its synthesis_config has no archetype.
    assert "archetype" not in out["stages"][0]["strategies"][0]["synthesis_config"]
    assert out["stages"][0]["strategies"][0]["synthesis_config"]["access_pattern"] == "random"
    assert out["stages"][1]["strategies"][0]["synthesis_config"]["archetype"] == "hash"


def test_apply_runtime_knob_routes_to_config() -> None:
    out = apply_adjustments(_instr(), [
        {"stage": "", "knob": "compute_ratio", "from": 0.5, "to": 0.8,
         "rationale": "", "expected_metric": "retiring", "expected_direction": "up"}
    ])
    assert out["config"]["compute_ratio"] == 0.8


def test_apply_unknown_knob_raises() -> None:
    with pytest.raises(ValueError, match="unknown knob"):
        apply_adjustments(_instr(), [
            {"stage": "mem_stage", "knob": "nope", "from": 1, "to": 2,
             "rationale": "", "expected_metric": "backend_bound", "expected_direction": "up"}
        ])


def test_apply_unknown_stage_raises() -> None:
    with pytest.raises(ValueError, match="unknown stage"):
        apply_adjustments(_instr(), [
            {"stage": "ghost", "knob": "working_set_mb", "from": 64, "to": 128,
             "rationale": "", "expected_metric": "backend_bound", "expected_direction": "up"}
        ])


def test_apply_runtime_knob_out_of_bounds_raises() -> None:
    with pytest.raises(ValueError, match="out of bounds"):
        apply_adjustments(_instr(), [
            {"stage": "", "knob": "compute_ratio", "from": 0.5, "to": 5.0,
             "rationale": "", "expected_metric": "retiring", "expected_direction": "up"}
        ])


def test_apply_archetype_invalid_enum_raises() -> None:
    with pytest.raises(ValueError, match="invalid value"):
        apply_adjustments(_instr(), [
            {"stage": "comp_stage", "knob": "archetype", "from": "matmul", "to": "bogus",
             "rationale": "", "expected_metric": "retiring", "expected_direction": "up"}
        ])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/agent/test_adjustment.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent.adjustment'`.

- [ ] **Step 3: Implement**

Create `src/agent/adjustment.py`:

```python
"""Deterministic adjustment mechanics for the Phase 2 auto-iteration loop.

Pure functions only — no LLM, no devkit, no I/O except apply_adjustments_to_config.
The loop driver (PR 3) wires these into run_iteration_loop.
"""

import json
import logging
import pathlib
from copy import deepcopy
from typing import Any

_log = logging.getLogger(__name__)

# --- Named knob space (RFC 0003 §1) + valid domains ---

# Structural knobs mutate stages[i].strategies[0].synthesis_config[knob].
STRUCTURAL_KNOBS: dict[str, dict[str, Any]] = {
    "working_set_mb": {"kind": "int", "min": 1, "max": 4096},
    "access_pattern": {"kind": "enum", "values": ("sequential", "mixed", "random")},
    "iterations": {"kind": "int", "min": 1, "max": 1_000_000},
    "archetype": {"kind": "enum", "values": ("compute", "hash", "matmul", "sort", "branch")},
}
# Runtime knobs mutate instruction["config"][knob].
RUNTIME_KNOBS: dict[str, dict[str, Any]] = {
    "compute_ratio": {"kind": "float", "min": 0.0, "max": 1.0},
    "memory_ratio": {"kind": "float", "min": 0.0, "max": 1.0},
    "thread_count": {"kind": "int", "min": 1, "max": 1024},
    "qps": {"kind": "int", "min": 1, "max": 1_000_000},
}
KNOB_DOMAINS: dict[str, dict[str, Any]] = {**STRUCTURAL_KNOBS, **RUNTIME_KNOBS}


def _validate_value(knob: str, to: Any) -> None:
    """Raise ValueError if `to` is outside the knob's valid domain."""
    if knob not in KNOB_DOMAINS:
        raise ValueError(f"unknown knob: {knob}")
    dom = KNOB_DOMAINS[knob]
    if dom["kind"] == "enum":
        if to not in dom["values"]:
            raise ValueError(f"invalid value {to!r} for {knob}; not in {dom['values']}")
    elif dom["kind"] in ("int", "float"):
        if not isinstance(to, (int, float)) or isinstance(to, bool):
            raise ValueError(f"{knob} must be a number, got {type(to).__name__}")
        if dom["kind"] == "int" and not float(to).is_integer():
            raise ValueError(f"{knob} must be an integer, got {to}")
        if not (dom["min"] <= to <= dom["max"]):
            raise ValueError(f"{knob}={to} out of bounds [{dom['min']}, {dom['max']}]")
    else:  # pragma: no cover - defensive
        raise ValueError(f"unknown domain kind for {knob}")


def apply_adjustments(
    instruction: dict[str, Any], adjustments: list[dict[str, Any]]
) -> dict[str, Any]:
    """Apply a list of adjustments to a copy of the instruction.

    Structural knobs route to stages[i].strategies[0].synthesis_config (matched
    by stage_name); runtime knobs route to instruction["config"][knob]. Raises
    on unknown stage/knob or out-of-domain `to`.
    """
    out = deepcopy(instruction)
    for adj in adjustments:
        knob = adj["knob"]
        _validate_value(knob, adj["to"])
        if knob in RUNTIME_KNOBS:
            out.setdefault("config", {})[knob] = adj["to"]
        else:
            stage_name = adj.get("stage", "")
            stage = next((s for s in out.get("stages", []) if s.get("stage_name") == stage_name), None)
            if stage is None:
                raise ValueError(f"unknown stage: {stage_name!r}")
            stage["strategies"][0]["synthesis_config"][knob] = adj["to"]
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_adjustment.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/adjustment.py tests/agent/test_adjustment.py
git commit -m "feat(adjustment): knob space, domains, apply_adjustments routing"
```

---

### Task 6: `apply_adjustments_to_config` (runtime subset writer)

**Files:**
- Modify: `src/agent/adjustment.py` (append function)
- Test: `tests/agent/test_adjustment.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/agent/test_adjustment.py`:

```python
import json
import pathlib


def test_apply_adjustments_to_config_writes_runtime_subset(tmp_path: pathlib.Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"compute_ratio": 0.5, "memory_ratio": 0.5, "thread_count": 4, "qps": 100}))
    from agent.adjustment import apply_adjustments_to_config
    apply_adjustments_to_config(cfg_path, [
        {"knob": "compute_ratio", "from": 0.5, "to": 0.8,
         "rationale": "", "expected_metric": "retiring", "expected_direction": "up"}
    ])
    data = json.loads(cfg_path.read_text())
    assert data["compute_ratio"] == 0.8
    assert data["memory_ratio"] == 0.5  # untouched


def test_apply_adjustments_to_config_ignores_structural(tmp_path: pathlib.Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"compute_ratio": 0.5}))
    from agent.adjustment import apply_adjustments_to_config
    apply_adjustments_to_config(cfg_path, [
        {"knob": "working_set_mb", "from": 64, "to": 256,
         "rationale": "", "expected_metric": "backend_bound", "expected_direction": "up"}
    ])
    # structural knob silently skipped (it doesn't live in config.json)
    assert json.loads(cfg_path.read_text()) == {"compute_ratio": 0.5}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/agent/test_adjustment.py -k to_config -q`
Expected: FAIL — `ImportError: cannot import name 'apply_adjustments_to_config'`.

- [ ] **Step 3: Implement**

Append to `src/agent/adjustment.py`:

```python
def apply_adjustments_to_config(
    config_path: pathlib.Path, adjustments: list[dict[str, Any]]
) -> None:
    """Read config.json, apply the runtime-knob subset of adjustments, write back.

    Structural knobs are skipped (they live in synthesis_config, not config.json).
    NOTE: this is a plain (non-atomic) writer used by PR 1 tooling/tests; the
    loop driver (PR 3) uses write_config_json_atomic for the crash-safe path.
    """
    data = json.loads(config_path.read_text())
    for adj in adjustments:
        knob = adj["knob"]
        if knob not in RUNTIME_KNOBS:
            continue  # structural knobs are not config.json entries
        _validate_value(knob, adj["to"])
        data[knob] = adj["to"]
    config_path.write_text(json.dumps(data, indent=2))
```

(`json` and `pathlib` are already imported at the top of the file from Task 5 — no new imports needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_adjustment.py -k to_config -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/adjustment.py tests/agent/test_adjustment.py
git commit -m "feat(adjustment): apply_adjustments_to_config runtime-subset writer"
```

---

### Task 7: `load_sensitivity` (+ fixture)

**Files:**
- Create: `tests/data/sensitivity_sample.json`
- Modify: `src/agent/adjustment.py` (append `load_sensitivity`)
- Test: `tests/agent/test_adjustment.py`

- [ ] **Step 1: Create the fixture**

Create `tests/data/sensitivity_sample.json` (matches the spike's output shape — note the `"expected"` field):

```json
{
  "rows": [],
  "verdicts": [
    {
      "knob": "working_set_mb",
      "target_metric": "backend_bound",
      "expected": "up",
      "verdict": "controllable",
      "values": [16, 64, 256],
      "metric_values": [12.06, 15.28, 16.41]
    },
    {
      "knob": "archetype",
      "target_metric": "retiring",
      "expected": "up",
      "verdict": "controllable",
      "values": ["compute", "hash", "matmul"],
      "metric_values": [22.87, 21.33, 63.91]
    },
    {
      "knob": "compute_ratio",
      "target_metric": "retiring",
      "expected": "up",
      "verdict": "controllable",
      "values": [0.2, 0.5, 0.8],
      "metric_values": [57.56, 63.82, 65.64]
    },
    {
      "knob": "memory_ratio",
      "target_metric": "backend_bound",
      "expected": "up",
      "verdict": "controllable",
      "values": [0.2, 0.5, 0.8],
      "metric_values": [11.19, 17.68, 19.08]
    },
    {
      "knob": "thread_count",
      "target_metric": "backend_bound",
      "expected": "dead",
      "reason": "all points errored"
    }
  ]
}
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/agent/test_adjustment.py`:

```python
def test_load_sensitivity_renames_expected_field(tmp_path: pathlib.Path) -> None:
    from agent.adjustment import load_sensitivity
    table = load_sensitivity(pathlib.Path(__file__).parent / "data" / "sensitivity_sample.json")
    e = table["working_set_mb"]
    assert e["target_metric"] == "backend_bound"
    assert e["expected_direction"] == "up"  # renamed from spike's "expected"
    assert e["verdict"] == "controllable"
    assert e["metric_values"] == [12.06, 15.28, 16.41]


def test_load_sensitivity_dead_knob_kept(tmp_path: pathlib.Path) -> None:
    from agent.adjustment import load_sensitivity
    table = load_sensitivity(pathlib.Path(__file__).parent / "data" / "sensitivity_sample.json")
    assert table["thread_count"]["verdict"] == "dead"
    assert table["thread_count"].get("expected_direction") in (None, "dead")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/agent/test_adjustment.py -k load_sensitivity -q`
Expected: FAIL — `ImportError: cannot import name 'load_sensitivity'`.

- [ ] **Step 4: Implement**

Append to `src/agent/adjustment.py`:

```python
def load_sensitivity(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    """Load the spike's sensitivity.json verdicts into a per-knob table.

    The spike writes each verdict with an `expected` field ("up"/"down"); this
    loader renames it to `expected_direction` (the name the controller / gate
    consult). A dead knob (no `expected`/`target_metric`) is kept with
    expected_direction=None so callers can see it is inert.
    """
    raw = json.loads(path.read_text())
    verdicts = raw.get("verdicts", raw if isinstance(raw, list) else [])
    table: dict[str, dict[str, Any]] = {}
    for v in verdicts:
        knob = v["knob"]
        table[knob] = {
            "target_metric": v.get("target_metric"),
            "expected_direction": v.get("expected"),
            "verdict": v.get("verdict", "dead"),
            "values": v.get("values", []),
            "metric_values": v.get("metric_values", []),
        }
    return table
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_adjustment.py -k load_sensitivity -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/data/sensitivity_sample.json src/agent/adjustment.py tests/agent/test_adjustment.py
git commit -m "feat(adjustment): load_sensitivity (renames spike 'expected' -> 'expected_direction')"
```

---

### Task 8: `validate_adjustments` (domain + tier-ownership + direction gate)

**Files:**
- Modify: `src/agent/adjustment.py` (append `validate_adjustments`)
- Test: `tests/agent/test_adjustment.py`

This is the largest task. The gate needs a helper to read a knob's actual current value and to determine a metric's current error sign from the report.

- [ ] **Step 1: Write the failing tests**

Append to `tests/agent/test_adjustment.py`:

```python
def _report(backend_diff: float = 0.0, retiring_diff: float = 0.0) -> dict:
    """A fake comparator report. diff_pct>0 means workload TOO HIGH vs customer."""
    return {
        "topdown_l1": {
            "backend_bound": {"diff_pct": backend_diff, "within_threshold": abs(backend_diff) <= 10.0},
            "retiring": {"diff_pct": retiring_diff, "within_threshold": abs(retiring_diff) <= 10.0},
            "frontend_bound": {"diff_pct": 0.0, "within_threshold": True},
            "bad_speculation": {"diff_pct": 0.0, "within_threshold": True},
        },
        "memory": {"bandwidth_gbps": {"diff_pct": 0.0, "within_threshold": True}},
        "hotspot_coverage": {"coverage_pct": 85.0},
        "convergence": {"converged": False, "reason": ""},
    }


_SENS = {
    "compute_ratio": {"target_metric": "retiring", "expected_direction": "up", "verdict": "controllable"},
    "memory_ratio": {"target_metric": "backend_bound", "expected_direction": "up", "verdict": "controllable"},
    "working_set_mb": {"target_metric": "backend_bound", "expected_direction": "up", "verdict": "controllable"},
}


def test_validate_wrong_direction_rejected() -> None:
    # backend_bound diff = +20 (too high). memory_ratio is an "up" knob on backend_bound,
    # so to LOWER backend we must DECREASE memory_ratio. Increasing it is wrong direction.
    instr = {"config": {"memory_ratio": 0.5}}
    from agent.adjustment import validate_adjustments
    accepted, rejected = validate_adjustments(
        [{"stage": "", "knob": "memory_ratio", "from": 0.5, "to": 0.8,
          "rationale": "", "expected_metric": "backend_bound", "expected_direction": "up"}],
        instr, _report(backend_diff=20.0), _SENS, tier="runtime",
    )
    assert accepted == []
    assert rejected[0]["reason"] == "wrong_direction"


def test_validate_correct_direction_accepted() -> None:
    instr = {"config": {"memory_ratio": 0.5}}
    from agent.adjustment import validate_adjustments
    accepted, rejected = validate_adjustments(
        [{"stage": "", "knob": "memory_ratio", "from": 0.5, "to": 0.2,
          "rationale": "", "expected_metric": "backend_bound", "expected_direction": "up"}],
        instr, _report(backend_diff=20.0), _SENS, tier="runtime",
    )
    assert len(accepted) == 1
    assert rejected == []


def test_validate_already_satisfied_rejected() -> None:
    # backend within threshold -> adjusting it is pointless churn.
    instr = {"config": {"memory_ratio": 0.5}}
    from agent.adjustment import validate_adjustments
    accepted, rejected = validate_adjustments(
        [{"stage": "", "knob": "memory_ratio", "from": 0.5, "to": 0.2,
          "rationale": "", "expected_metric": "backend_bound", "expected_direction": "up"}],
        instr, _report(backend_diff=2.0), _SENS, tier="runtime",
    )
    assert accepted == []
    assert rejected[0]["reason"] == "metric_already_satisfied"


def test_validate_secondary_metric_accepted() -> None:
    # backend is the LARGEST error (+20) but the adjustment targets retiring (also
    # unsatisfied, -15 = too low). retiring is "up"; workload too low -> increase.
    # The gate must ACCEPT this secondary-metric move (not largest-error coupled).
    instr = {"config": {"compute_ratio": 0.5}}
    from agent.adjustment import validate_adjustments
    accepted, _ = validate_adjustments(
        [{"stage": "", "knob": "compute_ratio", "from": 0.5, "to": 0.8,
          "rationale": "", "expected_metric": "retiring", "expected_direction": "up"}],
        instr, _report(backend_diff=20.0, retiring_diff=-15.0), _SENS, tier="runtime",
    )
    assert len(accepted) == 1


def test_validate_tier_ownership_drops_runtime_on_structural() -> None:
    instr = {"config": {"memory_ratio": 0.5}, "stages": []}
    from agent.adjustment import validate_adjustments
    accepted, rejected = validate_adjustments(
        [{"stage": "", "knob": "memory_ratio", "from": 0.5, "to": 0.2,
          "rationale": "", "expected_metric": "backend_bound", "expected_direction": "up"}],
        instr, _report(backend_diff=20.0), _SENS, tier="structural",
    )
    assert accepted == []
    assert rejected[0]["reason"] == "runtime_knob_not_owned_on_structural_tier"


def test_validate_tier_ownership_drops_structural_on_runtime() -> None:
    instr = {"stages": [{"stage_name": "mem_stage", "strategies": [{"synthesis_config": {"working_set_mb": 64}}]}]}
    from agent.adjustment import validate_adjustments
    accepted, rejected = validate_adjustments(
        [{"stage": "mem_stage", "knob": "working_set_mb", "from": 64, "to": 256,
          "rationale": "", "expected_metric": "backend_bound", "expected_direction": "up"}],
        instr, _report(backend_diff=20.0), _SENS, tier="runtime",
    )
    assert accepted == []
    assert rejected[0]["reason"] == "structural_knob_not_owned_on_runtime_tier"


def test_validate_from_mismatch_warns_but_keeps() -> None:
    # from=0.2 but actual=0.5; the gate re-derives 0.5->0.2 (correct direction) and accepts.
    instr = {"config": {"memory_ratio": 0.5}}
    from agent.adjustment import validate_adjustments
    accepted, rejected = validate_adjustments(
        [{"stage": "", "knob": "memory_ratio", "from": 0.2, "to": 0.2,
          "rationale": "", "expected_metric": "backend_bound", "expected_direction": "up"}],
        instr, _report(backend_diff=20.0), _SENS, tier="runtime",
    )
    assert len(accepted) == 1  # accepted despite stale from
    # (warning is logged, not returned; not assertable here without a log spy)


def test_validate_all_rejected_returns_empty_accepted() -> None:
    instr = {"config": {"memory_ratio": 0.5}}
    from agent.adjustment import validate_adjustments
    accepted, rejected = validate_adjustments(
        [{"stage": "", "knob": "memory_ratio", "from": 0.5, "to": 0.8,
          "rationale": "", "expected_metric": "backend_bound", "expected_direction": "up"}],
        instr, _report(backend_diff=20.0), _SENS, tier="runtime",
    )
    assert accepted == []
    assert len(rejected) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/agent/test_adjustment.py -k validate -q`
Expected: FAIL — `ImportError: cannot import name 'validate_adjustments'`.

- [ ] **Step 3: Implement**

Append to `src/agent/adjustment.py`:

```python
def _actual_current(instruction: dict[str, Any], adj: dict[str, Any]) -> Any:
    """Read the knob's actual current value from the instruction."""
    knob = adj["knob"]
    if knob in RUNTIME_KNOBS:
        return instruction.get("config", {}).get(knob)
    stage_name = adj.get("stage", "")
    stage = next((s for s in instruction.get("stages", []) if s.get("stage_name") == stage_name), None)
    if stage is None:
        return None
    return stage.get("strategies", [{}])[0].get("synthesis_config", {}).get(knob)


def _error_sign(report: dict[str, Any], metric: str, threshold: float) -> int:
    """+1 if workload metric is too high (diff_pct>0 beyond threshold), -1 too low, 0 satisfied."""
    diff = report.get("topdown_l1", {}).get(metric, {}).get("diff_pct", 0.0)
    if abs(diff) <= threshold:
        return 0
    return 1 if diff > 0 else -1


def validate_adjustments(
    adjustments: list[dict[str, Any]],
    instruction: dict[str, Any],
    report: dict[str, Any],
    sensitivity: dict[str, dict[str, Any]],
    tier: str,
    topdown_threshold_pct: float = 10.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Gate every adjustment list (deterministic or LLM) before apply_adjustments.

    Returns (accepted, rejected). Each rejected carries a `reason`.
    See spec §1b: domain, tier-ownership, direction (decoupled from largest
    error), from-mismatch (warn only).
    """
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for adj in adjustments:
        knob = adj["knob"]
        # Domain.
        if knob not in KNOB_DOMAINS:
            rejected.append({**adj, "reason": "unknown_knob"})
            continue
        try:
            _validate_value(knob, adj["to"])
        except ValueError as exc:
            rejected.append({**adj, "reason": f"domain_violation:{exc}"})
            continue

        # Tier ownership.
        is_runtime = knob in RUNTIME_KNOBS
        if tier == "structural" and is_runtime:
            rejected.append({**adj, "reason": "runtime_knob_not_owned_on_structural_tier"})
            continue
        if tier == "runtime" and not is_runtime:
            rejected.append({**adj, "reason": "structural_knob_not_owned_on_runtime_tier"})
            continue

        # Direction (decoupled from largest error): uses ACTUAL current, not from.
        actual = _actual_current(instruction, adj)
        to = adj["to"]
        entry = sensitivity.get(knob, {})
        metric = adj.get("expected_metric") or entry.get("target_metric")
        direction = adj.get("expected_direction") or entry.get("expected_direction")
        if metric is None or direction is None:
            rejected.append({**adj, "reason": "no_sensitivity_entry"})
            continue

        err = _error_sign(report, metric, topdown_threshold_pct)
        if err == 0:
            rejected.append({**adj, "reason": "metric_already_satisfied"})
            continue

        # Want to move metric DOWN (err=+1, too high) or UP (err=-1, too low).
        want_down = err > 0
        # direction "up" means knob raises metric; "down" means lowers it.
        knob_raises = direction == "up"
        # Required knob move sign to reduce error:
        #   want metric down + knob raises -> decrease knob (to<actual)
        #   want metric down + knob lowers  -> increase knob
        #   want metric up   + knob raises -> increase knob
        #   want metric up   + knob lowers  -> decrease knob
        want_increase = (want_down != knob_raises)  # XOR
        move_sign = (to - actual) if isinstance(to, (int, float)) and isinstance(actual, (int, float)) else 0
        move_up = move_sign > 0
        if (want_increase and not move_up) or (not want_increase and move_up) or move_sign == 0:
            rejected.append({**adj, "reason": "wrong_direction"})
            continue

        # from-mismatch: warn only.
        if adj.get("from") is not None and adj["from"] != actual:
            _log.warning(
                "adjustment from-mismatch: knob=%s from=%s actual=%s to=%s",
                knob, adj["from"], actual, to,
            )

        accepted.append(adj)

    return accepted, rejected
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_adjustment.py -k validate -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/adjustment.py tests/agent/test_adjustment.py
git commit -m "feat(adjustment): validate_adjustments gate (domain, tier-ownership, direction)"
```

---

### Task 9: `deterministic_revise` (runtime knobs only)

**Files:**
- Modify: `src/agent/adjustment.py` (append `deterministic_revise`)
- Test: `tests/agent/test_adjustment.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/agent/test_adjustment.py`:

```python
def test_deterministic_revise_picks_correct_runtime_knob_direction() -> None:
    # backend_bound too high (+20); memory_ratio is "up" on backend -> must DECREASE it.
    instr = {"config": {"compute_ratio": 0.5, "memory_ratio": 0.5, "thread_count": 4, "qps": 100}}
    from agent.adjustment import deterministic_revise
    adj = deterministic_revise(instr, _report(backend_diff=20.0), _SENS, IterationHistory(customer_name="t"))
    assert len(adj) == 1
    assert adj[0]["knob"] == "memory_ratio"
    assert adj[0]["to"] < 0.5  # decreased
    assert adj[0]["from"] == 0.5  # from == actual current


def test_deterministic_revise_clamps_to_bounds() -> None:
    # memory_ratio already at min 0.0 and backend too high -> would decrease past 0 -> clamp.
    instr = {"config": {"compute_ratio": 0.5, "memory_ratio": 0.0, "thread_count": 4, "qps": 100}}
    from agent.adjustment import deterministic_revise
    adj = deterministic_revise(instr, _report(backend_diff=20.0), _SENS, IterationHistory(customer_name="t"))
    assert adj[0]["to"] == 0.0


def test_deterministic_revise_never_emits_structural() -> None:
    instr = {"config": {"compute_ratio": 0.5, "memory_ratio": 0.5, "thread_count": 4, "qps": 100}}
    from agent.adjustment import deterministic_revise
    adj = deterministic_revise(instr, _report(backend_diff=20.0), _SENS, IterationHistory(customer_name="t"))
    for a in adj:
        assert a["knob"] in ("compute_ratio", "memory_ratio", "thread_count", "qps")


def test_deterministic_revise_skip_blocked_returns_empty() -> None:
    # If the only candidate knob was toggled within the oscillation window, return [].
    instr = {"config": {"compute_ratio": 0.5, "memory_ratio": 0.5, "thread_count": 4, "qps": 100}}
    hist = IterationHistory(customer_name="t")
    hist.add_record(IterationRecord(
        iteration=1, converged=False, topdown_diffs={"backend_bound": 20.0},
        applied_moves=[{"knob": "memory_ratio", "tier": "runtime", "sign": -1}],
    ))
    from agent.adjustment import deterministic_revise
    adj = deterministic_revise(instr, _report(backend_diff=20.0), _SENS, hist, oscillation_window=3)
    assert adj == []  # memory_ratio skip-blocked; no other runtime knob targets backend
```

Add the needed imports at the top of `tests/agent/test_adjustment.py` (below the existing `from agent.adjustment import apply_adjustments, KNOB_DOMAINS`):

```python
from observability.iteration_history import IterationHistory, IterationRecord
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/agent/test_adjustment.py -k deterministic_revise -q`
Expected: FAIL — `ImportError: cannot import name 'deterministic_revise'`.

- [ ] **Step 3: Implement**

Append to `src/agent/adjustment.py`:

```python
# Bounded steps per runtime knob (spec §3).
_STEP: dict[str, float] = {
    "compute_ratio": 0.2,
    "memory_ratio": 0.2,
    "thread_count": 1.0,
    "qps": 20.0,
}


def deterministic_revise(
    instruction: dict[str, Any],
    report: dict[str, Any],
    sensitivity: dict[str, dict[str, Any]],
    history: Any,
    oscillation_window: int = 3,
    topdown_threshold_pct: float = 10.0,
) -> list[dict[str, Any]]:
    """Assist controller: emit ONE runtime-knob adjustment that reduces the
    largest-error metric. Pure, no LLM.

    Returns [] if every candidate runtime knob is skip-blocked (toggled in the
    last `oscillation_window` iterations) -> forces escalation to the LLM tier.
    """
    # Largest-error topdown metric not within threshold.
    topdown = report.get("topdown_l1", {})
    candidates = [
        (m, abs(v.get("diff_pct", 0.0)))
        for m, v in topdown.items()
        if not v.get("within_threshold", True)
    ]
    if not candidates:
        return []
    candidates.sort(key=lambda x: x[1], reverse=True)
    target_metric, _ = candidates[0]
    err = _error_sign(report, target_metric, topdown_threshold_pct)
    if err == 0:
        return []
    want_down = err > 0

    # Knobs toggled within the window are skip-blocked.
    recent_moves: set[str] = set()
    for r in getattr(history, "records", [])[-oscillation_window:]:
        for mv in getattr(r, "applied_moves", []):
            recent_moves.add(str(mv["knob"]))

    # Find a runtime knob whose expected_direction reduces this metric.
    for knob, entry in sensitivity.items():
        if knob not in RUNTIME_KNOBS:
            continue
        if entry.get("verdict") != "controllable":
            continue
        if entry.get("target_metric") != target_metric:
            continue
        if knob in recent_moves:
            continue  # skip-blocked
        direction = entry.get("expected_direction")
        if direction not in ("up", "down"):
            continue
        knob_raises = direction == "up"
        want_increase = want_down != knob_raises  # XOR (same as the gate)
        actual = instruction.get("config", {}).get(knob)
        if not isinstance(actual, (int, float)):
            continue
        step = _STEP.get(knob, 0.1)
        to = actual + step if want_increase else actual - step
        dom = KNOB_DOMAINS[knob]
        to = max(dom["min"], min(dom["max"], to))  # clamp
        return [{
            "stage": "",
            "knob": knob,
            "from": actual,
            "to": to,
            "rationale": f"{target_metric} diff out of threshold; {direction} via {knob}",
            "expected_metric": target_metric,
            "expected_direction": direction,
        }]
    return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_adjustment.py -k deterministic_revise -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/adjustment.py tests/agent/test_adjustment.py
git commit -m "feat(adjustment): deterministic_revise runtime-knob controller with skip-block"
```

---

### Task 10: Full quality gate + PR

**Files:** none (verification + push)

- [ ] **Step 1: Run the full test suite (non-integration)**

Run: `python -m pytest tests/ -m "not integration" -q`
Expected: PASS, coverage ≥ 60%.

- [ ] **Step 2: Run lint + type checks**

Run: `python -m pre_commit run --all-files`
Expected: ruff, ruff-format, mypy strict (src/) all PASS. Fix any issues inline (e.g. add type annotations to public functions in `adjustment.py`; the `Any` typing is intentional for the dict-shaped instruction/report).

- [ ] **Step 3: Verify existing tests still green (no regressions)**

Run: `python -m pytest tests/observability/test_iteration_history.py tests/config/test_framework_config.py tests/agent/test_strategy.py -q`
Expected: PASS — the `IterationHistory` rewrite preserved the old `add_record`/`get_convergence_trend`/`is_converging`/`save`/`load` contract.

- [ ] **Step 4: Push the branch and open the PR**

```bash
git push -u origin feat/phase2-deterministic-leg
gh pr create --base main --title "Phase 2 PR 1: deterministic leg + gate" --body "Implements the pure, no-LLM half of the Phase 2 auto-iteration loop per the approved spec (docs/superpowers/specs/2026-08-17-phase2-auto-iteration-loop-design.md).

- adjustment.py: knob space + domains, apply_adjustments, apply_adjustments_to_config, load_sensitivity, validate_adjustments (domain + tier-ownership + direction gate decoupled from largest error), deterministic_revise
- iteration_history.py: compute_score (normalized multi-dim), applied_moves, observed_effects, is_oscillating (precise per-knob signed-move rule), no_improvement_for (best-refresh semantics), best_iteration excludes failed records
- ComparisonConfig: oscillation_window, no_improvement_stop, run_failure_stop, build_failure_stop, collect_retry

No LLM, no loop driver (PR 2 = revise_instruction, PR 3 = loop driver + DevkitConfig + atomic config writes). All unit-tested with fakes; no ARM/devkit/LLM.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 5: Update the project-state memory**

Note in the mirage memory file: PR 1 (deterministic leg + gate) opened; PR 2/PR 3 pending.

---

## Self-Review

**1. Spec coverage (PR 1 scope only):**
- `apply_adjustments` + routing + domain + raises → Task 5 ✓
- `apply_adjustments_to_config` → Task 6 ✓
- `load_sensitivity` (rename `expected`→`expected_direction`) → Task 7 ✓
- `validate_adjustments` (domain, tier-ownership, direction decoupled, from-warn) → Task 8 ✓
- `deterministic_revise` (largest-error, bounded step, clamp, skip-block→[]) → Task 9 ✓
- `score` (normalized multi-dim) → Task 2 ✓
- history: `adjustments`, `observed_effects`, `is_oscillating` (precise), `no_improvement_for` (best-refresh), `best_iteration` excludes failed → Tasks 2–4 ✓
- `ComparisonConfig` knobs (incl. `build_failure_stop`) → Task 1 ✓
- knob-space/domain validation → Task 5 ✓
- Unit tests for all of the above → embedded in each task ✓

PR 1 scope explicitly excludes: LLM `revise_instruction` (PR 2), loop driver, `DevkitConfig`, atomic writes, `write_config_json_atomic`, error model, degradation, stub-plant integration test (all PR 3). None of those appear as tasks. ✓

**2. Placeholder scan:** No TBD/TODO; every code step has full code; test code is complete. ✓

**3. Type consistency:**
- `compute_score(record, comparison=None)` defined Task 2, used in `add_record` (Task 2) and `no_improvement_for` (Task 3) — same name. ✓
- `validate_adjustments(..., tier, topdown_threshold_pct=10.0)` — `tier` is the param the loop passes (PR 3); tests pass `tier=`. ✓
- `deterministic_revise(instruction, report, sensitivity, history, oscillation_window=3, topdown_threshold_pct=10.0)` matches the corrected spec signature. ✓
- `IterationRecord.applied_moves` entries are `{"knob","tier","sign"}` — produced by the loop (PR 3), consumed by `is_oscillating` (Task 4) and the skip-block set in `deterministic_revise` (Task 9). ✓
- `KNOB_DOMAINS`, `RUNTIME_KNOBS`, `STRUCTURAL_KNOBS` referenced consistently across Tasks 5–9. ✓
- `_error_sign`, `_actual_current` defined in Task 8, reused by `deterministic_revise` (Task 9). ✓

No issues found.
