"""Unified Result models for all harness components.

All results use pydantic BaseModel for consistency, serialization, and validation.
"""

from pydantic import BaseModel


class BuildResult(BaseModel):
    """Result of a build attempt."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    binary_path: str | None = None
    duration_seconds: float = 0.0


class ExecutionResult(BaseModel):
    """Result of a workload execution."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_seconds: float = 0.0


class CollectionResult(BaseModel):
    """Result of a metrics collection attempt."""

    success: bool
    topdown_path: str | None = None
    flamegraph_path: str | None = None
    memory_path: str | None = None
    error: str = ""


class RunFailure(BaseModel):
    """A run/collect failure from run_and_collect (crash / timeout / collect_fail)."""

    reason: str
    kind: str  # "crash" | "timeout" | "collect_fail"
    stdout: str = ""
    stderr: str = ""


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
