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
