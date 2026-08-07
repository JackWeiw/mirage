"""Runtime configuration for workload execution."""

from pydantic import BaseModel, Field


class RunConfig(BaseModel):
    """Configuration for running a workload binary."""

    thread_count: int = 4
    qps: int = 100
    warmup_seconds: int = 30
    measurement_seconds: int = 60
    ramp_up_seconds: int = 10
    config_path: str = "config.json"
    concurrency: int = Field(default=4, description="Number of concurrent stress threads")
