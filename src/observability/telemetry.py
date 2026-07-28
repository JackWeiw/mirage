"""Pipeline state tracking and timing metrics."""

import time
from enum import StrEnum

from pydantic import BaseModel, Field


class PipelineState(StrEnum):
    """Pipeline execution states."""

    IDLE = "idle"
    INGESTING = "ingesting"
    GENERATING = "generating"
    BUILDING = "building"
    RUNNING = "running"
    COLLECTING = "collecting"
    COMPARING = "comparing"
    ITERATING = "iterating"
    CONVERGED = "converged"
    FAILED = "failed"


class StepTiming(BaseModel):
    """Timing for one pipeline step."""

    step: str
    start_time: float = 0.0
    end_time: float = 0.0
    duration_seconds: float = 0.0
    success: bool = True
    error: str | None = None


class PipelineTelemetry(BaseModel):
    """Telemetry data for a full pipeline run."""

    pipeline_id: str = ""
    state: PipelineState = PipelineState.IDLE
    steps: list[StepTiming] = Field(default_factory=list)
    current_step: str | None = None

    def start_step(self, step_name: str) -> None:
        """Start tracking a pipeline step."""
        self.current_step = step_name
        valid_states = [s.value for s in PipelineState]
        state_value = step_name.lower() if step_name.lower() in valid_states else PipelineState.IDLE
        self.state = PipelineState(state_value)
        self.steps.append(StepTiming(step=step_name, start_time=time.time()))

    def end_step(self, step_name: str, success: bool = True, error: str | None = None) -> None:
        """End tracking a pipeline step."""
        for step in self.steps:
            if step.step == step_name and step.end_time == 0.0:
                step.end_time = time.time()
                step.duration_seconds = step.end_time - step.start_time
                step.success = success
                step.error = error
                break
        self.current_step = None

    def total_duration(self) -> float:
        """Total pipeline duration in seconds."""
        return sum(s.duration_seconds for s in self.steps)

    def get_summary(self) -> dict[str, object]:
        """Get a summary of the pipeline run."""
        return {
            "pipeline_id": self.pipeline_id,
            "state": self.state.value,
            "total_duration": self.total_duration(),
            "steps": [
                {"step": s.step, "duration": s.duration_seconds, "success": s.success}
                for s in self.steps
            ],
        }
