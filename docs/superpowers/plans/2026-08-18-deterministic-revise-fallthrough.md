# deterministic_revise fall-through (issue #56)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the iter-0 dead-end where `deterministic_revise` picks the single largest-error metric as its sole target, finds no steerable runtime knob for it, and returns `[]` even when a smaller-error metric IS steerable.

**Architecture:** Restructure the single `for knob in sensitivity` loop (target fixed to `candidates[0]`) into a nested loop: `for target_metric, _ in candidates: for knob in sensitivity: ...`. The outer loop walks candidate metrics largest-first; the moment a metric yields a valid (controllable, not skip-blocked, not boundary-exhausted) knob, return that adjustment. `[]` is returned only when EVERY candidate metric is unsteerable / skip-blocked / boundary-exhausted.

**Tech Stack:** Python 3.13, pydantic v2, pytest, ruff + mypy strict, pre-commit.

**Scope:** Part A only (the fall-through). The relative-vs-absolute `diff_pct` issue (#46) stays separate; this fix is correct under either interpretation because it only changes WHICH candidate metric is targeted, not how diff_pct is computed.

---

## Context

`src/agent/adjustment.py::deterministic_revise` (lines 253-324) currently:

```python
candidates.sort(key=lambda x: (-x[1], x[0]))  # largest error first
target_metric, _ = candidates[0]              # <-- pins to the single largest
err = _error_sign(report, target_metric, topdown_threshold_pct)
...
for knob, entry in sensitivity.items():
    ...
    if entry.get("target_metric") != target_metric:   # <-- skips every other metric
        continue
    ...
return []   # <-- dead-end if the largest metric has no steerable knob
```

On the real Kunpeng ARM run, `frontend_bound` (5.14 customer value) blows up to a huge *relative* `diff_pct` and tops the queue, but NO runtime knob targets `frontend_bound` (the runtime knobs target `retiring` and `backend_bound`). So the loop skips every knob and returns `[]` on iteration 0 -> `runtime_tier_exhausted_agent_unavailable` stop, even though `backend_bound` (the real customer bottleneck) is steerable via `memory_ratio`.

The fix: fall through the candidate metrics largest-first, returning the first steerable one.

## Files
- Modify: `src/agent/adjustment.py` (function `deterministic_revise`, ~lines 267-324)
- Test: `tests/agent/test_adjustment.py` (new test + keep existing 6 green)

## Task 1: Fall through candidate metrics

- [ ] **Step 1: Write the failing test**

Add to `tests/agent/test_adjustment.py` (after `test_deterministic_revise_no_runtime_knob_targets_metric_returns_empty`):

```python
def test_deterministic_revise_falls_through_unsteerable_largest_metric() -> None:
    # Largest error is frontend_bound (no runtime knob targets it); the fix must
    # fall through to the next-largest steerable metric (backend_bound) instead
    # of dead-ending on iter 0 (issue #56).
    instr = {"config": {"compute_ratio": 0.5, "memory_ratio": 0.5, "thread_count": 4, "qps": 100}}
    report = {
        "topdown_l1": {
            "backend_bound": {"diff_pct": 20.0, "within_threshold": False},
            "retiring": {"diff_pct": 0.0, "within_threshold": True},
            "frontend_bound": {"diff_pct": 30.0, "within_threshold": False},
            "bad_speculation": {"diff_pct": 0.0, "within_threshold": True},
        },
        "memory": {"bandwidth_gbps": {"diff_pct": 0.0, "within_threshold": True}},
        "hotspot_coverage": {"coverage_pct": 85.0},
        "convergence": {"converged": False, "reason": ""},
    }
    from agent.adjustment import deterministic_revise

    adj = deterministic_revise(instr, report, _SENS, IterationHistory(customer_name="t"))
    assert len(adj) == 1
    assert adj[0]["knob"] == "memory_ratio"  # targets backend_bound (the steerable one)
    assert adj[0]["expected_metric"] == "backend_bound"
    assert adj[0]["to"] < 0.5  # backend too high + memory_ratio "up" => decrease
```

- [ ] **Step 2: Run test to verify it fails**

`PYTHONPATH=src python -m pytest tests/agent/test_adjustment.py::test_deterministic_revise_falls_through_unsteerable_largest_metric -q`
Expected: FAIL (returns `[]` because frontend_bound tops the queue and has no steerable knob).

- [ ] **Step 3: Implement the fix**

Replace the body of `deterministic_revise` from the `candidates.sort(...)` line through `return []` with:

```python
    candidates.sort(key=lambda x: (-x[1], x[0]))  # largest error first; name breaks ties

    # Knobs toggled within the window are skip-blocked.
    recent_moves: set[str] = set()
    for r in getattr(history, "records", [])[-oscillation_window:]:
        for mv in getattr(r, "applied_moves", []):
            recent_moves.add(str(mv["knob"]))

    # Fall through the candidate metrics largest-first: the largest-error metric
    # may have no steerable runtime knob (e.g. frontend_bound has none), in which
    # case we try the next-largest metric instead of dead-ending on iter 0 (#56).
    for target_metric, _ in candidates:
        err = _error_sign(report, target_metric, topdown_threshold_pct)
        if err == 0:
            continue
        want_down = err > 0

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
            if not isinstance(actual, int | float) or isinstance(actual, bool):
                continue
            step = _STEP.get(knob, 0.1)
            to = actual + step if want_increase else actual - step
            dom = KNOB_DOMAINS[knob]
            to = max(dom["min"], min(dom["max"], to))  # clamp
            if to == actual:
                continue  # at boundary, can't move in the wanted direction
            return [
                {
                    "stage": "",
                    "knob": knob,
                    "from": actual,
                    "to": to,
                    "rationale": f"{target_metric} diff out of threshold; {direction} via {knob}",
                    "expected_metric": target_metric,
                    "expected_direction": direction,
                }
            ]
    return []
```

Note: the `_error_sign`/`want_down` computation moves INSIDE the outer loop (it depends on `target_metric`). The `recent_moves` computation stays BEFORE the loop (computed once).

- [ ] **Step 4: Run tests to verify they pass**

`PYTHONPATH=src python -m pytest tests/agent/test_adjustment.py -q`
Expected: PASS (new test + all 6 existing `deterministic_revise` tests green).

The existing `test_deterministic_revise_no_runtime_knob_targets_metric_returns_empty` (only `bad_speculation` out of threshold) still returns `[]` because `bad_speculation` is the ONLY candidate -> no smaller metric to fall through to.

- [ ] **Step 5: Commit**

```bash
git add src/agent/adjustment.py tests/agent/test_adjustment.py
git commit -m "fix(agent): deterministic_revise falls through unsteerable largest metric (#56)"
```

## Verification
- `PYTHONPATH=src python -m pytest tests/ -q` (cov >= 60%)
- `python -m ruff format src/ tests/` + `python -m ruff check src/ tests/`
- `python -m mypy --config-file=pyproject.toml src/ tests/`
- `python -m pre_commit run --all-files`

## PR
- Branch `fix/deterministic-revise-fallthrough` off `main`.
- Title: `deterministic_revise falls through unsteerable largest metric (#56)`
- Two-stage review (spec + quality), squash-merge, delete branch.

## Out of scope
- #46 (relative vs absolute `diff_pct`) — separate issue.
- Re-running the real-ARM loop (after both #55 and #56 merge).
