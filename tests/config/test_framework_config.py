"""Tests for FrameworkConfig."""

from config.framework_config import AgentConfig, ComparisonConfig, FrameworkConfig


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
