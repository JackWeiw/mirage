"""Framework configuration model — loaded from YAML."""

import pathlib

import yaml
from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    model: str = "claude-sonnet-5"
    max_tokens: int = 4096
    api_key: str | None = None


class ComparisonConfig(BaseModel):
    topdown_threshold_pct: float = 10.0
    memory_threshold_pct: float = 5.0
    coverage_threshold_pct: float = 80.0
    oscillation_window: int = 3  # iters looked back for knob direction reversals
    no_improvement_stop: int = 3  # K consecutive iters w/o a new best score -> stop
    run_failure_stop: int = 2  # consecutive run/collect failures -> stop
    build_failure_stop: int = 2  # consecutive structural build failures -> stop
    collect_retry: int = 1  # retries on a transient collect/timeout


class RunDefaults(BaseModel):
    warmup_seconds: int = 30
    measurement_seconds: int = 60
    thread_count: int = 4
    qps: int = 100


class CodegenConfig(BaseModel):
    compile_flags: str = "-O2 -march=armv8.2-a"
    default_dependencies: list[dict[str, object]] = Field(default_factory=list)


class HarnessConfig(BaseModel):
    cmake_path: str = "cmake"
    make_path: str = "make"
    build_dir_suffix: str = "build"


class FrameworkConfig(BaseModel):
    """Complete framework configuration."""

    log_level: str = "INFO"
    json_logging: bool = False
    agent: AgentConfig = Field(default_factory=AgentConfig)
    comparison: ComparisonConfig = Field(default_factory=ComparisonConfig)
    run_defaults: RunDefaults = Field(default_factory=RunDefaults)
    codegen: CodegenConfig = Field(default_factory=CodegenConfig)
    harness: HarnessConfig = Field(default_factory=HarnessConfig)

    @classmethod
    def from_yaml(cls, filepath: pathlib.Path) -> "FrameworkConfig":
        """Load configuration from a YAML file."""
        with open(filepath) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data.get("framework", data))

    @classmethod
    def defaults(cls) -> "FrameworkConfig":
        """Return default configuration."""
        defaults_path = pathlib.Path(__file__).parent / "default_config.yaml"
        return cls.from_yaml(defaults_path)
