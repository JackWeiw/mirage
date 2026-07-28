"""Compare two Profiles and produce a structured diff report with convergence assessment."""

from typing import Any

from config.framework_config import ComparisonConfig
from profile.profile_schema import HotspotFunction, MemoryProfile, Profile, TopdownL1


class ProfileComparator:
    """Compare a customer Profile with a workload Profile.

    Uses ComparisonConfig from FrameworkConfig for thresholds.
    """

    def __init__(self, config: ComparisonConfig | None = None) -> None:
        self.config = config or ComparisonConfig()
        self.topdown_threshold_pct = self.config.topdown_threshold_pct
        self.memory_threshold_pct = self.config.memory_threshold_pct
        self.coverage_threshold_pct = self.config.coverage_threshold_pct

    def compare(
        self,
        customer_profile: Profile,
        workload_profile: Profile,
        iteration: int = 0,
    ) -> dict[str, Any]:
        """Compare customer and workload Profiles.

        Args:
            customer_profile: The target Profile from customer data.
            workload_profile: The Profile from the generated workload run.
            iteration: Current iteration number.

        Returns:
            Comparison report dict compatible with IterationRecord creation.
        """
        topdown_l1_report = self._compare_topdown_l1(
            customer_profile.topdown, workload_profile.topdown
        )

        memory_report = self._compare_memory(customer_profile.memory, workload_profile.memory)

        coverage_report = self._compare_hotspot_coverage(
            customer_profile.hotspots, workload_profile.hotspots
        )

        # Convergence check
        all_topdown_ok = (
            all(v["within_threshold"] for v in topdown_l1_report.values())
            if topdown_l1_report
            else True
        )
        memory_ok = (
            memory_report.get("bandwidth_gbps", {}).get("within_threshold", True)
            if memory_report
            else True
        )
        coverage_ok = coverage_report["coverage_pct"] >= self.coverage_threshold_pct

        converged = all_topdown_ok and memory_ok and coverage_ok

        not_ok_metrics: list[str] = []
        for name, v in topdown_l1_report.items():
            if not v["within_threshold"]:
                not_ok_metrics.append(f"topdown.{name} diff_pct {v['diff_pct']:.1f}%")
        if not memory_ok:
            bw = memory_report.get("bandwidth_gbps", {})
            not_ok_metrics.append(f"memory.bandwidth diff_pct {bw.get('diff_pct', 0):.1f}%")
        if not coverage_ok:
            not_ok_metrics.append(
                f"hotspot coverage {coverage_report['coverage_pct']:.1f}% "
                f"< {self.coverage_threshold_pct}%"
            )

        reason = (
            "All metrics within thresholds"
            if converged
            else "Exceeds threshold: " + ", ".join(not_ok_metrics)
        )

        recommendation = self._make_recommendation(topdown_l1_report, coverage_report)

        return {
            "iteration": iteration,
            "topdown_l1": topdown_l1_report,
            "memory": memory_report,
            "hotspot_coverage": coverage_report,
            "convergence": {
                "converged": converged,
                "reason": reason,
                "iteration_count": iteration,
            },
            "recommendation": recommendation,
        }

    def _compare_topdown_l1(
        self, customer: TopdownL1 | None, workload: TopdownL1 | None
    ) -> dict[str, Any]:
        if customer is None or workload is None:
            return {}
        metrics = ["frontend_bound", "backend_bound", "bad_speculation", "retiring"]
        report: dict[str, Any] = {}
        for m in metrics:
            c_val = getattr(customer, m)
            w_val = getattr(workload, m)
            diff = w_val - c_val
            diff_pct = (diff / c_val) * 100.0 if c_val != 0 else 0.0
            within = abs(diff_pct) <= self.topdown_threshold_pct
            report[m] = {
                "customer": c_val,
                "workload": w_val,
                "diff": diff,
                "diff_pct": diff_pct,
                "within_threshold": within,
            }
        return report

    def _compare_memory(
        self, customer: MemoryProfile | None, workload: MemoryProfile | None
    ) -> dict[str, Any]:
        if customer is None or workload is None:
            return {}
        report: dict[str, Any] = {}
        if customer.bandwidth_gbps is not None and workload.bandwidth_gbps is not None:
            diff = workload.bandwidth_gbps - customer.bandwidth_gbps
            diff_pct = (diff / customer.bandwidth_gbps) * 100.0
            report["bandwidth_gbps"] = {
                "customer": customer.bandwidth_gbps,
                "workload": workload.bandwidth_gbps,
                "diff": diff,
                "diff_pct": diff_pct,
                "within_threshold": abs(diff_pct) <= self.memory_threshold_pct,
            }
        return report

    def _compare_hotspot_coverage(
        self,
        customer_hotspots: list[HotspotFunction],
        workload_hotspots: list[HotspotFunction],
    ) -> dict[str, Any]:
        open_source_hotspots = [h for h in customer_hotspots if h.source == "open_source"]
        if not open_source_hotspots:
            return {
                "total_open_source_hotspots": 0,
                "covered_in_workload": 0,
                "coverage_pct": 100.0,
                "missed_hotspots": [],
            }

        workload_func_names = {h.function for h in workload_hotspots}
        covered = [h for h in open_source_hotspots if h.function in workload_func_names]
        missed = [h for h in open_source_hotspots if h.function not in workload_func_names]

        coverage_pct = (len(covered) / len(open_source_hotspots)) * 100.0

        return {
            "total_open_source_hotspots": len(open_source_hotspots),
            "covered_in_workload": len(covered),
            "coverage_pct": coverage_pct,
            "missed_hotspots": [h.function for h in missed],
        }

    def _make_recommendation(
        self, topdown_report: dict[str, Any], coverage_report: dict[str, Any]
    ) -> str:
        """Generate iteration strategy recommendation."""
        not_ok = {k: v for k, v in topdown_report.items() if not v["within_threshold"]}

        if not not_ok:
            if coverage_report["coverage_pct"] < self.coverage_threshold_pct:
                return "Priority 2: 调 Behavior Profile — 增加未覆盖的热点函数调用"
            return "已收敛, 无需调整"

        max_diff = max(abs(v["diff_pct"]) for v in not_ok.values())
        if max_diff < 5.0:
            return "Priority 1: 调 config.json 参数 — 微调并发/QPS/内存比例"
        return "Priority 2: 调 Behavior Profile — 调整行为实现策略和权重"
