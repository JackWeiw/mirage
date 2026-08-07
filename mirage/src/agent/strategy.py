"""Iteration strategy decision logic."""

from typing import Any

from config.framework_config import ComparisonConfig


def decide_iteration_priority(
    comparison_report: dict[str, Any], config: ComparisonConfig | None = None
) -> int:
    """Decide which iteration priority level to use based on comparison report.

    Priority levels (cost increasing):
    0: Converged — no further iteration needed
    1: Adjust config.json parameters
    2: Adjust Behavior Profiles
    3: Adjust Business Workflow
    4: Adjust Service Skeleton

    Args:
        comparison_report: Output from ProfileComparator.compare()
        config: ComparisonConfig for threshold values. If None, uses defaults.

    Returns:
        Priority level 0-4.
    """
    if config is None:
        config = ComparisonConfig()

    topdown_report = comparison_report.get("topdown_l1", {})
    not_ok = {k: v for k, v in topdown_report.items() if not v.get("within_threshold", True)}

    if not not_ok:
        coverage = comparison_report.get("hotspot_coverage", {}).get("coverage_pct", 100.0)
        if coverage < config.coverage_threshold_pct:
            return 2
        return 0

    max_diff = max(abs(v.get("diff_pct", 0)) for v in not_ok.values())
    if max_diff < 5.0:
        return 1
    elif max_diff < config.topdown_threshold_pct:
        return 2
    elif max_diff < 20.0:
        return 3
    else:
        return 4
