"""Tests for IterationHistory."""

import pathlib
import tempfile

from observability.iteration_history import IterationHistory, IterationRecord, compute_score


def test_iteration_history_add_record() -> None:
    history = IterationHistory(customer_name="test")
    record = IterationRecord(
        iteration=1,
        converged=False,
        topdown_diffs={"frontend_bound": -12.0, "backend_bound": -5.0},
        coverage_pct=70.0,
    )
    history.add_record(record)
    assert history.total_iterations == 1
    assert history.best_iteration == 1


def test_iteration_history_convergence_trend() -> None:
    history = IterationHistory(customer_name="test")
    for i, (fb_diff, bb_diff) in enumerate([(-12.0, -5.0), (-10.0, -4.0), (-8.0, -3.0)]):
        history.add_record(
            IterationRecord(
                iteration=i + 1,
                converged=False,
                topdown_diffs={"frontend_bound": fb_diff, "backend_bound": bb_diff},
                coverage_pct=70.0 + i * 5,
            )
        )
    trend = history.get_convergence_trend()
    assert len(trend) == 3
    assert trend[0]["total_diff"] > trend[1]["total_diff"] > trend[2]["total_diff"]


def test_iteration_history_is_converging() -> None:
    history = IterationHistory(customer_name="test")
    for i, diffs in enumerate([(-12.0, -5.0), (-10.0, -4.0), (-8.0, -3.0)]):
        history.add_record(
            IterationRecord(
                iteration=i + 1,
                converged=False,
                topdown_diffs={"frontend_bound": diffs[0], "backend_bound": diffs[1]},
            )
        )
    assert history.is_converging() is True


def test_iteration_history_not_converging() -> None:
    history = IterationHistory(customer_name="test")
    for i, diffs in enumerate([(-8.0, -3.0), (-10.0, -4.0), (-12.0, -5.0)]):
        history.add_record(
            IterationRecord(
                iteration=i + 1,
                converged=False,
                topdown_diffs={"frontend_bound": diffs[0], "backend_bound": diffs[1]},
            )
        )
    assert history.is_converging() is False


def test_iteration_history_save_and_load() -> None:
    tmpdir = pathlib.Path(tempfile.mkdtemp())
    history = IterationHistory(customer_name="test")
    history.add_record(IterationRecord(iteration=1, converged=False, topdown_diffs={"fb": -10.0}))
    filepath = history.save(tmpdir / "history.json")
    loaded = IterationHistory.load(filepath)
    assert loaded.customer_name == "test"
    assert len(loaded.records) == 1


def test_iteration_history_best_iteration_updates() -> None:
    history = IterationHistory(customer_name="test")
    history.add_record(
        IterationRecord(iteration=1, converged=False, topdown_diffs={"fb": -12.0, "bb": -5.0})
    )
    history.add_record(
        IterationRecord(iteration=2, converged=False, topdown_diffs={"fb": -8.0, "bb": -3.0})
    )  # Better
    assert history.best_iteration == 2


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


def test_build_failed_records_excluded_from_best_iteration() -> None:
    history = IterationHistory(customer_name="t")
    good = IterationRecord(iteration=1, converged=False, topdown_diffs={"b": 4.0})
    bad_build = IterationRecord(iteration=2, converged=False, topdown_diffs={}, build_failed=True)
    history.add_record(good)
    history.add_record(bad_build)
    assert history.best_iteration == 1  # build failure excluded; best stays the good one


def test_recent_adjustments() -> None:
    history = IterationHistory(customer_name="t")
    adj_a = [{"knob": "a", "to": 1.0}]
    adj_b = [{"knob": "b", "to": 2.0}]
    adj_c = [{"knob": "c", "to": 3.0}, {"knob": "d", "to": 4.0}]
    for i, adj in enumerate([adj_a, adj_b, adj_c]):
        history.add_record(
            IterationRecord(
                iteration=i + 1, converged=False, topdown_diffs={"b": 5.0}, adjustments=adj
            )
        )
    # last 2 records → b + c adjustments (flat)
    result_2 = history.recent_adjustments(2)
    assert result_2 == [
        {"knob": "b", "to": 2.0},
        {"knob": "c", "to": 3.0},
        {"knob": "d", "to": 4.0},
    ]
    # n > len(records) is safe: Python list slicing just returns all
    result_all = history.recent_adjustments(10)
    assert result_all == [
        {"knob": "a", "to": 1.0},
        {"knob": "b", "to": 2.0},
        {"knob": "c", "to": 3.0},
        {"knob": "d", "to": 4.0},
    ]
    # n<=0 returns all records' adjustments (records[-0:] == records[0:])
    result_zero = history.recent_adjustments(0)
    assert result_zero == result_all
