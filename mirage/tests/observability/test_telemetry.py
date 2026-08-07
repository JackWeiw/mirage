"""Tests for PipelineTelemetry."""

import time

from observability.telemetry import PipelineState, PipelineTelemetry


def test_pipeline_telemetry_step_tracking() -> None:
    tel = PipelineTelemetry(pipeline_id="test_run")
    tel.start_step("ingesting")
    time.sleep(0.01)
    tel.end_step("ingesting", success=True)
    assert tel.steps[0].step == "ingesting"
    assert tel.steps[0].success is True
    assert tel.steps[0].duration_seconds > 0


def test_pipeline_telemetry_failed_step() -> None:
    tel = PipelineTelemetry(pipeline_id="test_run")
    tel.start_step("building")
    tel.end_step("building", success=False, error="cmake not found")
    assert tel.steps[0].success is False
    assert tel.steps[0].error == "cmake not found"


def test_pipeline_telemetry_summary() -> None:
    tel = PipelineTelemetry(pipeline_id="test_run")
    tel.start_step("ingesting")
    time.sleep(0.01)
    tel.end_step("ingesting", success=True)
    summary = tel.get_summary()
    assert summary["pipeline_id"] == "test_run"
    assert len(summary["steps"]) == 1  # type: ignore[arg-type]


def test_pipeline_telemetry_state_transition() -> None:
    tel = PipelineTelemetry(pipeline_id="test_run")
    assert tel.state == PipelineState.IDLE
    tel.start_step("ingesting")
    assert tel.state.value == "ingesting"


def test_pipeline_telemetry_invalid_state_stays_idle() -> None:
    tel = PipelineTelemetry(pipeline_id="test_run")
    assert tel.state == PipelineState.IDLE
    tel.start_step("unknown_step")
    assert tel.state == PipelineState.IDLE  # unknown step name, stays IDLE
