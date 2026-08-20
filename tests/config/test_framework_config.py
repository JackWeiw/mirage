"""Tests for FrameworkConfig."""

import pathlib

import pytest

from config.framework_config import (
    AgentConfig,
    ComparisonConfig,
    DevkitConfig,
    FrameworkConfig,
)


def test_framework_config_defaults() -> None:
    config = FrameworkConfig.defaults()
    assert config.log_level == "INFO"
    assert config.comparison.topdown_threshold_pct == 10.0
    # default_config.yaml's model must stay in sync with AgentConfig's default;
    # assert the invariant rather than a hardcoded id so a bump doesn't rot here.
    assert config.agent.model == AgentConfig().model


def test_framework_config_from_yaml() -> None:
    config = FrameworkConfig.defaults()
    assert config.harness.cmake_path == "cmake"
    assert config.run_defaults.warmup_seconds == 30


def test_framework_config_comparison_thresholds() -> None:
    config = FrameworkConfig.defaults()
    assert config.comparison.memory_threshold_pct == 5.0
    assert config.comparison.coverage_threshold_pct == 80.0


def test_framework_config_codegen() -> None:
    config = FrameworkConfig.defaults()
    assert config.codegen.compile_flags == "-O2 -march=armv8.2-a"
    assert len(config.codegen.default_dependencies) >= 1


def test_comparison_config_loop_control_defaults() -> None:
    c = ComparisonConfig()
    assert c.oscillation_window == 3
    assert c.no_improvement_stop == 3
    assert c.run_failure_stop == 2
    assert c.build_failure_stop == 2
    assert c.collect_retry == 1
    # Existing thresholds unchanged.
    assert c.topdown_threshold_pct == 10.0
    assert c.memory_threshold_pct == 5.0
    assert c.coverage_threshold_pct == 80.0


def test_devkit_config_defaults() -> None:
    d = DevkitConfig()
    assert d.devkit_cmd is None
    assert d.duration_seconds == 20
    assert d.interval_seconds == 3
    assert d.cpu_range is None
    assert d.collect_pid is True


def test_framework_config_has_devkit_field() -> None:
    config = FrameworkConfig.defaults()
    assert config.devkit.devkit_cmd is None
    assert config.devkit.duration_seconds == 20
    assert config.devkit.interval_seconds == 3
    assert config.devkit.collect_pid is True


def test_framework_config_devkit_from_yaml() -> None:
    # default_config.yaml carries the devkit section; values round-trip.
    config = FrameworkConfig.defaults()
    assert config.devkit.duration_seconds == 20
    assert config.devkit.interval_seconds == 3
    assert config.devkit.collect_pid is True
    assert config.devkit.cpu_range is None


def test_agent_config_defaults_base_url_none_provider_anthropic() -> None:
    cfg = AgentConfig()
    assert cfg.base_url is None
    assert cfg.provider == "anthropic"


def test_agent_config_accepts_openai_provider_with_base_url() -> None:
    cfg = AgentConfig(provider="openai", base_url="https://gw.example.com/v1", api_key="k")
    assert cfg.provider == "openai"
    assert cfg.base_url == "https://gw.example.com/v1"


def test_agent_config_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError):
        AgentConfig(provider="gemini")


_AGENT_ENVS = (
    "MIRAGE_AGENT_API_KEY",
    "MIRAGE_AGENT_BASE_URL",
    "MIRAGE_AGENT_PROVIDER",
    "MIRAGE_AGENT_MODEL",
    "MIRAGE_AGENT_MAX_TOKENS",
)


def _clear_agent_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _AGENT_ENVS:
        monkeypatch.delenv(var, raising=False)


def test_from_env_no_env_is_offline_like_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without MIRAGE_AGENT_* env, from_env == defaults (agent offline)."""
    _clear_agent_envs(monkeypatch)
    fw = FrameworkConfig.from_env()
    assert fw.agent.api_key is None
    assert fw.agent.base_url is None
    assert fw.agent.provider == "anthropic"
    # matches defaults() so tests/callers that don't set env stay deterministic
    defaults = FrameworkConfig.defaults()
    assert fw.agent.model == defaults.agent.model


def test_from_env_applies_agent_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_agent_envs(monkeypatch)
    monkeypatch.setenv("MIRAGE_AGENT_API_KEY", "sk-test")
    monkeypatch.setenv("MIRAGE_AGENT_BASE_URL", "https://gw.example.com")
    monkeypatch.setenv("MIRAGE_AGENT_PROVIDER", "openai")
    monkeypatch.setenv("MIRAGE_AGENT_MODEL", "gpt-4o")
    fw = FrameworkConfig.from_env()
    assert fw.agent.api_key == "sk-test"
    assert fw.agent.base_url == "https://gw.example.com"
    assert fw.agent.provider == "openai"
    assert fw.agent.model == "gpt-4o"


def test_from_env_overrides_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """MIRAGE_AGENT_MAX_TOKENS raises the budget for reasoning models (GLM-4.7 /
    deepseek-r1 burn thousands of tokens on reasoning; the 4096 default
    truncates them mid-thought before the JSON answer)."""
    _clear_agent_envs(monkeypatch)
    monkeypatch.setenv("MIRAGE_AGENT_API_KEY", "sk-test")
    monkeypatch.setenv("MIRAGE_AGENT_MAX_TOKENS", "16384")
    fw = FrameworkConfig.from_env()
    assert fw.agent.max_tokens == 16384


def test_from_env_rejects_non_int_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-integer MIRAGE_AGENT_MAX_TOKENS fails loud at load time."""
    _clear_agent_envs(monkeypatch)
    monkeypatch.setenv("MIRAGE_AGENT_API_KEY", "sk-test")
    monkeypatch.setenv("MIRAGE_AGENT_MAX_TOKENS", "big")
    with pytest.raises(ValueError):
        FrameworkConfig.from_env()


def test_from_env_rejects_invalid_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invalid MIRAGE_AGENT_PROVIDER fails loud at load time (not silently
    routed to the wrong client shape later)."""
    _clear_agent_envs(monkeypatch)
    monkeypatch.setenv("MIRAGE_AGENT_API_KEY", "sk-test")
    monkeypatch.setenv("MIRAGE_AGENT_PROVIDER", "gemini")
    with pytest.raises(ValueError):
        FrameworkConfig.from_env()


def test_from_env_env_overrides_yaml_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Precedence: yaml < env — env wins over a key set in the config file."""
    _clear_agent_envs(monkeypatch)
    cfg_yaml = tmp_path / "fw.yaml"
    cfg_yaml.write_text(
        "framework:\n"
        "  agent:\n"
        "    model: claude-opus-5\n"
        "    api_key: sk-from-file\n"
        "    base_url: https://gw.yaml.example\n"
        "    provider: anthropic\n"
    )
    monkeypatch.setenv("MIRAGE_AGENT_API_KEY", "sk-from-env")
    fw = FrameworkConfig.from_env(config_path=cfg_yaml)
    # env beats file
    assert fw.agent.api_key == "sk-from-env"
    # file value survives where env is unset
    assert fw.agent.base_url == "https://gw.yaml.example"
    assert fw.agent.provider == "anthropic"
    assert fw.agent.model == "claude-opus-5"
