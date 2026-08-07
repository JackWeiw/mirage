"""Tests for IterationHistory."""

import pathlib
import tempfile

from observability.iteration_history import IterationHistory, IterationRecord


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
