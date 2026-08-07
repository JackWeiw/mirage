"""Tests for iteration strategy decision logic."""

from agent.strategy import decide_iteration_priority
from config.framework_config import ComparisonConfig


def test_strategy_converged() -> None:
    report = {
        "topdown_l1": {
            "frontend_bound": {"within_threshold": True, "diff_pct": 3.0},
            "backend_bound": {"within_threshold": True, "diff_pct": 4.0},
            "bad_speculation": {"within_threshold": True, "diff_pct": 2.0},
            "retiring": {"within_threshold": True, "diff_pct": 5.0},
        },
        "hotspot_coverage": {"coverage_pct": 85.0},
    }
    priority = decide_iteration_priority(report)
    assert priority == 0


def test_strategy_priority_1_small_diffs() -> None:
    report = {
        "topdown_l1": {
            "frontend_bound": {"within_threshold": False, "diff_pct": 4.0},
            "backend_bound": {"within_threshold": True, "diff_pct": 3.0},
        },
        "hotspot_coverage": {"coverage_pct": 90.0},
    }
    priority = decide_iteration_priority(report)
    assert priority == 1


def test_strategy_priority_2_moderate_diffs() -> None:
    report = {
        "topdown_l1": {
            "frontend_bound": {"within_threshold": False, "diff_pct": -8.0},
            "backend_bound": {"within_threshold": True, "diff_pct": 3.0},
        },
        "hotspot_coverage": {"coverage_pct": 85.0},
    }
    priority = decide_iteration_priority(report)
    assert priority == 2


def test_strategy_priority_3_large_diffs() -> None:
    report = {
        "topdown_l1": {
            "frontend_bound": {"within_threshold": False, "diff_pct": -15.0},
        },
        "hotspot_coverage": {"coverage_pct": 70.0},
    }
    priority = decide_iteration_priority(report)
    assert priority == 3


def test_strategy_priority_4_very_large_diffs() -> None:
    report = {
        "topdown_l1": {
            "frontend_bound": {"within_threshold": False, "diff_pct": -25.0},
        },
        "hotspot_coverage": {"coverage_pct": 50.0},
    }
    priority = decide_iteration_priority(report)
    assert priority == 4


def test_strategy_converged_but_low_coverage() -> None:
    report = {
        "topdown_l1": {
            "frontend_bound": {"within_threshold": True, "diff_pct": 3.0},
        },
        "hotspot_coverage": {"coverage_pct": 70.0},
    }
    priority = decide_iteration_priority(report)
    assert priority == 2


def test_strategy_with_custom_config() -> None:
    config = ComparisonConfig(topdown_threshold_pct=15.0)
    report = {
        "topdown_l1": {
            "frontend_bound": {"within_threshold": False, "diff_pct": -6.0},
        },
        "hotspot_coverage": {"coverage_pct": 90.0},
    }
    # With wider threshold (15%), 6% diff is below threshold, so priority 2
    priority = decide_iteration_priority(report, config=config)
    assert priority == 2
