"""Tests for FrameworkConfig."""

from config.framework_config import FrameworkConfig


def test_framework_config_defaults() -> None:
    config = FrameworkConfig.defaults()
    assert config.log_level == "INFO"
    assert config.comparison.topdown_threshold_pct == 10.0
    assert config.agent.model == "claude-sonnet-4-6"


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
