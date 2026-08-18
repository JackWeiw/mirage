# Absolute percentage-point diff_pct + unit normalization (#46)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the comparator emit absolute percentage-point `diff_pct` for Topdown L1 (matching what the downstream loop already assumes), and normalize the Topdown L1 unit to percentages across all parse paths so the absolute formula is correct regardless of source.

**Architecture:** Two coupled fixes. (1) `ProfileComparator._compare_topdown_l1` changes `diff_pct` from relative `(w-c)/c*100` to absolute `(w - c)` percentage points — topdown metrics ARE percentages, so the percentage-point difference is the natural error. (2) Topdown L1 values become percentages (0-100) everywhere: `parse_text` already returns percentages; the JSON/CSV fixtures and test `TopdownL1(...)` constructions (currently fractions summing to 1.0) are normalized to percentages so `parse_json`/`parse_csv` pass-through stays honest. Memory bandwidth keeps its relative formula (GB/s, large denominator, no small-base amplification — out of scope).

**Tech Stack:** Python 3.13, pydantic v2, pytest, ruff + mypy strict, pre-commit.

**Scope:** #46 Part A+B (the comparator relative->absolute + the units reconciliation across parse paths). This is the real-ARM-run blocker: relative `diff_pct` amplified tiny-customer-value metrics (frontend 5.14 -> 133% relative) so they dominated priority; with absolute pp, frontend 5.14 vs workload 12 -> 6.86pp (within a 10pp threshold) instead of 133%.

---

## Why absolute pp (not relative)

The downstream loop ALREADY treats `diff_pct` as absolute percentage points:
- `agent/adjustment.py::validate_adjustments` / `deterministic_revise`: `_error_sign` uses `abs(diff_pct) <= topdown_threshold_pct` and `decide_iteration_priority` thresholds (5/10/20).
- `agent/adjustment.py` tests build fake reports with `diff_pct=20.0` meaning "20pp off" (threshold 10 -> not within).
- `strategy.py::decide_iteration_priority` ranks by `max(abs(diff_pct))` with pp thresholds.

Only the comparator produces relative. It is simply wrong vs. the loop's contract. Switching it to absolute pp makes the comparator consistent with everything downstream.

Topdown L1 metrics are percentages that sum to 100%. The percentage-point difference `w - c` is the natural, base-invariant error metric: frontend 5.14 -> 12 is a 6.86pp gap (sensible, base-invariant), NOT a 133% relative gap (which falsely dominates priority by penalizing small-customer-value metrics).

## Files
- Modify: `src/profile/comparator.py` (`_compare_topdown_l1` formula + comment)
- Modify: `tests/data/sample_topdown.json`, `tests/data/sample_topdown.csv`, `tests/data/sample_profile.json`, `tests/data/sample_workload_profile.json` (topdown fractions -> percentages)
- Modify: `tests/profile/test_comparator.py`, `tests/profile/test_profile_store.py`, `tests/profile/test_profile_schema.py`, `tests/agent/test_pipeline.py`, `tests/harness/test_run_iteration_loop.py`, `tests/ingestion/test_topdown_parser.py` (TopdownL1 fractions -> percentages; diff_pct expectations -> absolute)
- No production change to `topdown_parser.py` (parse_json/parse_csv stay pass-through; fixtures now store percentages, matching parse_text). Schema docstring updated to state the percentage contract.

## Task 1: Comparator -> absolute percentage points

- [ ] **Step 1: Write the failing test**

Add to `tests/profile/test_comparator.py` (a new test pinning the absolute semantics on a small-customer-value metric — the #46 regression):

```python
def test_topdown_diff_pct_is_absolute_pp_not_relative() -> None:
    # Topdown values are percentages; diff_pct must be the percentage-point
    # difference (w - c), NOT the relative (w-c)/c*100. A small-customer-value
    # metric (frontend 5.14) must NOT blow up to a huge relative diff (#46).
    cust = Profile(topdown=TopdownL1(frontend_bound=5.14, backend_bound=72.79,
                                     bad_speculation=3.0, retiring=19.07))
    work = Profile(topdown=TopdownL1(frontend_bound=12.0, backend_bound=70.0,
                                     bad_speculation=3.0, retiring=15.0))
    rep = ProfileComparator().compare(cust, work)
    # 12.0 - 5.14 = 6.86 pp (absolute), NOT (12-5.14)/5.14*100 = 133.5% relative.
    assert abs(rep["topdown_l1"]["frontend_bound"]["diff_pct"] - 6.86) < 0.01
    # within 10pp threshold now (was 133% -> not within).
    assert rep["topdown_l1"]["frontend_bound"]["within_threshold"] is True
```

- [ ] **Step 2: Run test to verify it fails**

`PYTHONPATH=src python -m pytest tests/profile/test_comparator.py::test_topdown_diff_pct_is_absolute_pp_not_relative -q`
Expected: FAIL (current formula yields 133.5, not 6.86).

- [ ] **Step 3: Implement the fix**

In `src/profile/comparator.py::_compare_topdown_l1`, replace the relative formula:

```python
            diff = w_val - c_val
            # diff_pct is ABSOLUTE percentage points (w - c): topdown L1 metrics
            # are themselves percentages, so the pp gap is the base-invariant
            # error. The relative (w-c)/c*100 amplified small-customer-value
            # metrics (e.g. frontend 5.14 -> 133%) and let them dominate priority
            # (#46). This matches the downstream loop's contract (the gate and
            # decide_iteration_priority already treat diff_pct as pp).
            diff_pct = float(diff)
            within = abs(diff_pct) <= self.topdown_threshold_pct
```

Leave `_compare_memory` unchanged (bandwidth is GB/s; relative % is the natural metric and the denominator is large, so no small-base amplification).

- [ ] **Step 4: Run the comparator tests to see expected failures from the contract change**

`PYTHONPATH=src python -m pytest tests/profile/test_comparator.py -q`
Expected: the new test PASSES; existing fraction-based tests FAIL (their TopdownL1 uses fractions 0.25 etc. and assert e.g. diff_pct == -12.0). These are fixed in Task 3.

## Task 2: Normalize fixtures to percentages

- [ ] **Step 1: `tests/data/sample_topdown.json`** — multiply topdown L1 AND L2 values by 100:
  - L1: 0.25->25.0, 0.40->40.0, 0.10->10.0, 0.25->25.0
  - L2 frontend: 0.05->5.0, 0.15->15.0, 0.05->5.0
  - L2 backend: 0.30->30.0, 0.10->10.0
  - L2 bad_spec: 0.08->8.0, 0.02->2.0
  - L2 retiring: 0.15->15.0, 0.10->10.0
  - memory: UNCHANGED (45.2 GB/s, 0.08 l3, 0.02 tlb, 512 MB).

- [ ] **Step 2: `tests/data/sample_topdown.csv`** — multiply topdown L1+L2 value column by 100 (frontend_bound 0.25->25.0 ... retiring.light_ops 0.10->10.0); memory.* rows UNCHANGED.

- [ ] **Step 3: `tests/data/sample_profile.json`** — `topdown`: 0.25->25.0, 0.40->40.0, 0.10->10.0, 0.25->25.0. (No L2 here.) memory unchanged.

- [ ] **Step 4: `tests/data/sample_workload_profile.json`** — `topdown`: 0.22->22.0, 0.38->38.0, 0.11->11.0, 0.29->29.0. memory unchanged.

## Task 3: Update tests to percentage + absolute-pp semantics

For every test that constructs `TopdownL1(frontend_bound=0.25, ...)` (fraction), multiply each field by 100. For every `diff_pct` assertion, recompute under absolute pp (w - c).

Files with fraction `TopdownL1(...)`:
- `tests/profile/test_comparator.py` (lines ~39-40, 71-72; diff_pct expectation ~line 84: -12.0 -> -3.0; plus any others in the file)
- `tests/profile/test_profile_store.py` (lines ~15-16)
- `tests/profile/test_profile_schema.py` (lines ~49, ~80-81)
- `tests/agent/test_pipeline.py` (lines ~98, ~166)
- `tests/harness/test_run_iteration_loop.py` (lines ~32, ~177, ~521, ~574, ~719)

Files with parse-based expectations (fixtures now percentages):
- `tests/ingestion/test_topdown_parser.py` — parse_json/parse_csv expectations change from 0.25->25.0 etc.; parse_text expectations already percentages (unchanged).

- [ ] **Step 1: Update `tests/profile/test_comparator.py`**

Read the full file. Multiply every `TopdownL1(...)` field by 100 (e.g. `frontend_bound=0.25` -> `25.0`). Recompute every `diff_pct` assertion as `w - c` (e.g. customer frontend 25.0, workload 22.0 -> diff_pct -3.0, not -12.0). Keep the new Task-1 test as-is (already percentages).

- [ ] **Step 2: Update the remaining 4 test files' `TopdownL1(...)` constructions**

Mechanical: multiply each topdown field by 100 in: `test_profile_store.py`, `test_profile_schema.py`, `test_pipeline.py`, `test_run_iteration_loop.py`. If any test asserts a `diff_pct` value, recompute as `w - c`.

- [ ] **Step 3: Update `tests/ingestion/test_topdown_parser.py`**

parse_json/parse_csv assertions: the parsed TopdownL1 now holds the fixture's percentages (25.0/40.0/10.0/25.0), not fractions. Update the equality assertions. parse_text assertions (sample_topdown.txt) unchanged.

- [ ] **Step 4: Run the full suite**

`PYTHONPATH=src python -m pytest tests/ -q`
Expected: ALL PASS (292 + new test). Iterate any remaining fraction/diff_pct misses until green.

## Task 4: Schema docstring + gate + PR

- [ ] **Step 1: Document the percentage contract**

In `src/profile/profile_schema.py`, add/update the `TopdownL1` docstring to state: "All L1 fields are PERCENTAGES (0-100), summing to ~100. Parsers (parse_json/parse_csv/parse_text) yield percentages; the comparator computes absolute percentage-point diffs."

- [ ] **Step 2: Full gate**

```bash
python -m ruff format src/ tests/
python -m ruff check src/ tests/
PYTHONPATH=src python -m pytest tests/ -q   # cov >= 60%
PYTHONPATH=src python -m mypy --config-file=pyproject.toml src/ tests/
python -m pre_commit run --all-files
```

- [ ] **Step 3: Commit + PR**

Branch `fix/comparator-absolute-diff-pct`. Title: `comparator: absolute percentage-point diff_pct + unit normalization (#46)`. Two-stage review (spec + quality), squash-merge, delete branch.

## Verification
- All tests green, cov >= 60%, mypy clean, pre-commit clean.
- Manual sanity: the #46 regression test (frontend 5.14 vs 12 -> 6.86pp within 10pp) passes — the real-ARM priority distortion is gone.

## Out of scope
- Memory bandwidth diff_pct (stays relative; GB/s, no amplification).
- Re-running real-ARM (user-side, after merge).
- L2 topdown comparison (not compared; fixtures normalized for consistency only).
