"""Framework configuration model — loaded from YAML."""

import os
import pathlib

import yaml
from pydantic import BaseModel, Field, field_validator

# Operator-facing LLM-gateway env vars (mirage-prefixed, provider-agnostic —
# the same MIRAGE_AGENT_* set drives every entry point that uses from_env).
# Declared at module scope (not as a class attr) so pydantic doesn't treat it as
# a private attribute / field. ``base_url`` is the operator's gateway, never a
# vendor's official host.
_AGENT_ENV_MAP: dict[str, str] = {
    "api_key": "MIRAGE_AGENT_API_KEY",
    "base_url": "MIRAGE_AGENT_BASE_URL",
    "provider": "MIRAGE_AGENT_PROVIDER",
    "model": "MIRAGE_AGENT_MODEL",
    "max_tokens": "MIRAGE_AGENT_MAX_TOKENS",
}


class AgentConfig(BaseModel):
    model: str = "claude-sonnet-5"
    max_tokens: int = 4096
    # repr=False so the key never appears in repr(AgentConfig) / accidental log
    # lines (issue #55 security spec: api_key travels only in SDK auth headers).
    api_key: str | None = Field(default=None, repr=False)
    # Custom LLM endpoint (issue #55): the operator's gateway, never a vendor's
    # official endpoint. None -> the SDK's own default host (degraded/local-only
    # when api_key is also None). ``provider`` selects the endpoint shape.
    base_url: str | None = None
    provider: str = "anthropic"

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, v: str) -> str:
        if v not in ("anthropic", "openai"):
            raise ValueError(f"provider must be 'anthropic' or 'openai', got {v!r}")
        return v


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


class DevkitConfig(BaseModel):
    """Devkit topdown-collection plumbing for the auto-iteration loop (#47).

    The loop's run_and_collect calls collect_topdown(duration=..., interval=...,
    pid=...) and pins the workload with taskset to cpu_range. None devkit_cmd
    means the devkit is not configured (degraded / no-collection mode).
    """

    devkit_cmd: str | None = None
    duration_seconds: int = 20
    interval_seconds: int = 3
    cpu_range: str | None = None  # taskset pin, e.g. "4" or "4-7"
    collect_pid: bool = True  # -p <pid> attribution (spike-proven)


class FrameworkConfig(BaseModel):
    """Complete framework configuration."""

    log_level: str = "INFO"
    json_logging: bool = False
    agent: AgentConfig = Field(default_factory=AgentConfig)
    comparison: ComparisonConfig = Field(default_factory=ComparisonConfig)
    run_defaults: RunDefaults = Field(default_factory=RunDefaults)
    codegen: CodegenConfig = Field(default_factory=CodegenConfig)
    harness: HarnessConfig = Field(default_factory=HarnessConfig)
    devkit: DevkitConfig = Field(default_factory=DevkitConfig)

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

    @classmethod
    def from_env(cls, config_path: pathlib.Path | None = None) -> "FrameworkConfig":
        """Load from a yaml (or ``defaults()``) then apply ``MIRAGE_AGENT_*`` env
        overrides. Precedence: yaml < env.

        This is the operator-facing loader for the LLM gateway — use it wherever a
        FrameworkConfig should honor the deployment environment (Pipeline's default
        config, example drivers). ``defaults()`` deliberately stays env-free so
        tests get deterministic offline configs.

        Env vars (all optional; ``MIRAGE_AGENT_API_KEY`` is what flips the agent
        online — ``AgentCore`` builds a client iff ``api_key is not None``):
          MIRAGE_AGENT_API_KEY    -> agent.api_key   (operator's gateway key)
          MIRAGE_AGENT_BASE_URL   -> agent.base_url  (the gateway, never a vendor host)
          MIRAGE_AGENT_PROVIDER   -> agent.provider  ("anthropic" | "openai")
          MIRAGE_AGENT_MODEL      -> agent.model
          MIRAGE_AGENT_MAX_TOKENS -> agent.max_tokens (reasoning models like
            GLM-4.7 / deepseek-r1 burn thousands of tokens on reasoning before the
            JSON answer; the 4096 default truncates them mid-thought.)
        """
        fw = cls.from_yaml(config_path) if config_path is not None else cls.defaults()
        overrides = {
            field: os.environ[name]
            for field, name in _AGENT_ENV_MAP.items()
            if os.environ.get(name) is not None
        }
        if overrides:
            # Reconstruct agent via __init__ so AgentConfig's provider
            # field_validator re-runs (model_copy / setattr skip it; make_client
            # silently routes any non-"openai" provider to the Anthropic shape,
            # so an invalid MIRAGE_AGENT_PROVIDER must fail loud at load time).
            fw.agent = AgentConfig(
                model=overrides.get("model", fw.agent.model),
                max_tokens=int(overrides.get("max_tokens", fw.agent.max_tokens)),
                api_key=overrides.get("api_key", fw.agent.api_key),
                base_url=overrides.get("base_url", fw.agent.base_url),
                provider=overrides.get("provider", fw.agent.provider),
            )
        return fw
