"""Stub-plant integration test for Pipeline.run_synthesis_iteration_loop.

Exercises the plan-centric two-tier loop (Phase D) using a stub plant (no
ARM/devkit/LLM). Mirrors test_run_iteration_loop but the plant stubs operate
on a SynthesisPlan (collect(binary, plan) / build(plan)) and the structural
tier is surgical re-synthesis (architect.revise_plan clears targeted modules'
cached bodies), not knob gating. The REAL deterministic controller + REAL
gate + REAL comparator are kept; only the plant + architect are stubbed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent.synthesis_plan import SynthesisPlan
from codegen.module_graph_builder import ModuleGraphBuilder
from config.framework_config import FrameworkConfig
from harness.pipeline import Pipeline
from models.results import BuildResult, PipelineResult, RunFailure
from profile.profile_schema import CallTreeNode, Profile, ProfileMetadata, TopdownL1

if TYPE_CHECKING:
    import pathlib


def _customer_profile() -> Profile:
    """Target customer profile (topdown-only convergence)."""
    return Profile(
        metadata=ProfileMetadata(customer="stub", date="2026-09-19"),
        topdown=TopdownL1(
            frontend_bound=10.0,
            backend_bound=72.0,
            bad_speculation=5.0,
            retiring=13.0,
        ),
        memory=None,
        hotspots=[],
    )


def _call_tree_profile() -> Profile:
    """main -> ns_a::foo() -> ns_b::bar() (drives the ModuleGraphBuilder)."""
    root = CallTreeNode(
        function="main",
        library="custom",
        source="customer_custom",
        self_samples=0,
        cumulative_samples=100,
        depth=0,
        children=[
            CallTreeNode(
                function="ns_a::foo()",
                library="custom",
                source="customer_custom",
                self_samples=40,
                cumulative_samples=80,
                depth=1,
                children=[
                    CallTreeNode(
                        function="ns_b::bar()",
                        library="custom",
                        source="customer_custom",
                        self_samples=40,
                        cumulative_samples=40,
                        depth=2,
                    ),
                ],
            ),
        ],
    )
    return Profile(
        metadata=ProfileMetadata(customer="acme", date="2026-09-19"),
        call_tree=[root],
    )


def _seed_plan() -> SynthesisPlan:
    """A SynthesisPlan whose module_graph.config carries the runtime knobs the
    loop's runtime tier mutates (memory_ratio etc.)."""
    graph = ModuleGraphBuilder().build(_call_tree_profile(), project_name="acme")
    plan = SynthesisPlan.from_graph(graph, source="seed")
    plan.module_graph.config = {
        "compute_ratio": 0.1,
        "memory_ratio": 0.1,
        "thread_count": 4,
        "qps": 100,
        "warmup_seconds": 0,
        "measurement_seconds": 1,
    }
    return plan


def _sensitivity() -> dict[str, dict[str, Any]]:
    """Sensitivity table for the runtime knobs."""
    return {
        "memory_ratio": {
            "target_metric": "backend_bound",
            "expected_direction": "up",
            "verdict": "controllable",
            "values": [],
            "metric_values": [],
        },
        "compute_ratio": {
            "target_metric": "retiring",
            "expected_direction": "up",
            "verdict": "controllable",
            "values": [],
            "metric_values": [],
        },
        "thread_count": {
            "target_metric": "frontend_bound",
            "expected_direction": "up",
            "verdict": "controllable",
            "values": [],
            "metric_values": [],
        },
        "qps": {
            "target_metric": "bad_speculation",
            "expected_direction": "down",
            "verdict": "controllable",
            "values": [],
            "metric_values": [],
        },
    }


def _make_config(**overrides: Any) -> FrameworkConfig:
    """FrameworkConfig.defaults() with comparison-threshold overrides."""
    cfg = FrameworkConfig.defaults()
    for k, v in overrides.items():
        if hasattr(cfg.comparison, k):
            setattr(cfg.comparison, k, v)
    return cfg


def _fake_build(plan: SynthesisPlan) -> BuildResult:
    return BuildResult(success=True, binary_path="/fake/binary")


def _make_collect_stub(base_backend: float = 50.0, mr_slope: float = 5.0) -> Any:
    """backend_bound = base_backend + memory_ratio*mr_slope.

    Seed mr=0.1 -> 50.5, gap -21.5pp vs customer 72 (OUT, >=20pp -> priority
    >= 2 -> structural when an agent is present; runtime-only when degraded).
    mr_slope=5 caps runtime memory_ratio at 55 (mr=1.0) -> still -17pp OUT ->
    degraded runs exhaust the runtime tier instead of falsely converging.
    """

    def collect(binary: str, plan: SynthesisPlan) -> Profile | RunFailure:
        mr: Any = plan.module_graph.config.get("memory_ratio", 0.1)
        return Profile(
            metadata=ProfileMetadata(customer="workload", date="2026-09-19"),
            topdown=TopdownL1(
                frontend_bound=10.0,
                backend_bound=base_backend + mr * mr_slope,
                bad_speculation=5.0,
                retiring=13.0,
            ),
            memory=None,
            hotspots=[],
        )

    return collect


# Path 1: degraded (agent offline) -> runtime-tier-only -> exhausts + stops.


class TestDegradedPath:
    """Agent unavailable; structural gap (priority >= 2) forces runtime-tier-only;
    the runtime memory_ratio knob can't close a -21pp gap -> exhausts + stops."""

    def test_degraded_runtime_only(self, tmp_path: pathlib.Path) -> None:
        pipeline = Pipeline(
            output_base_dir=tmp_path,
            config=_make_config(oscillation_window=1, no_improvement_stop=100),
        )
        result = pipeline.run_synthesis_iteration_loop(
            customer_profile=_customer_profile(),
            seed_plan=_seed_plan(),
            sensitivity=_sensitivity(),
            max_iter=10,
            collect=_make_collect_stub(),
            build=_fake_build,
        )
        assert result.degraded is True
        assert result.stop_reason == "runtime_tier_exhausted_agent_unavailable"
        assert result.best_iteration is not None
        assert len(pipeline.history.records) >= 1


# Path 2: structural tier -> architect.revise_plan -> rebuild from revised plan.


class TestStructuralTierPath:
    """priority >= 2 + agent available -> architect.revise_plan clears a module's
    cached body -> the loop rebuilds from the revised plan (orchestrator
    re-synthesizes cache-misses + rebuilds). Verifies the structural wiring."""

    def test_structural_revise_plan_triggers_rebuild(self, tmp_path: pathlib.Path) -> None:
        seed = _seed_plan()
        build_count = 0

        class _MockArchitect:
            def is_available(self) -> bool:
                return True

            def design_plan(self, profile: Profile, top_k: int = 20) -> SynthesisPlan:
                return seed

            def revise_plan(
                self,
                plan: SynthesisPlan,
                report: dict[str, Any],
                sensitivity: dict[str, dict[str, Any]],
                history: Any,
            ) -> tuple[SynthesisPlan, list[dict[str, Any]]]:
                revised = plan.model_copy(deep=True)
                revised.synthesized_bodies.pop("ns_a", None)  # surgical clear
                return revised, [{"module": "ns_a", "reason": "backend_bound gap"}]

        def tracking_build(plan: SynthesisPlan) -> BuildResult:
            nonlocal build_count
            build_count += 1
            return BuildResult(success=True, binary_path="/fake/binary")

        pipeline = Pipeline(
            output_base_dir=tmp_path,
            config=_make_config(no_improvement_stop=100, oscillation_window=100),
        )
        result = pipeline.run_synthesis_iteration_loop(
            customer_profile=_customer_profile(),
            seed_plan=seed,
            sensitivity=_sensitivity(),
            max_iter=3,
            collect=_make_collect_stub(),
            build=tracking_build,
            architect=_MockArchitect(),  # type: ignore[arg-type]
        )
        assert build_count >= 2  # seed build + >= 1 structural rebuild
        assert isinstance(result, PipelineResult)


# Path 3: build-failure self-correction via pending_build_fix.


class TestBuildFailureSelfCorrectionPath:
    """A structural rebuild fails once; the next iteration skips collect (dead
    binary), revises from last_report, and rebuilds -- recovering instead of
    false-terminating on build_failure_streak. Mirrors run_iteration_loop's
    pending_build_fix path."""

    def test_build_failure_self_corrects(self, tmp_path: pathlib.Path) -> None:
        seed = _seed_plan()
        build_count = 0

        class _MockArchitect:
            def is_available(self) -> bool:
                return True

            def design_plan(self, profile: Profile, top_k: int = 20) -> SynthesisPlan:
                return seed

            def revise_plan(
                self,
                plan: SynthesisPlan,
                report: dict[str, Any],
                sensitivity: dict[str, dict[str, Any]],
                history: Any,
            ) -> tuple[SynthesisPlan, list[dict[str, Any]]]:
                revised = plan.model_copy(deep=True)
                return revised, [{"module": "ns_a", "reason": "fix compile error"}]

        def build_fail_once(plan: SynthesisPlan) -> BuildResult:
            nonlocal build_count
            build_count += 1
            if build_count == 2:  # first structural rebuild fails
                return BuildResult(
                    success=False, stderr="error: use of undeclared identifier 'bar'"
                )
            return BuildResult(success=True, binary_path="/fake/binary")

        pipeline = Pipeline(
            output_base_dir=tmp_path,
            config=_make_config(
                build_failure_stop=5, no_improvement_stop=100, oscillation_window=100
            ),
        )
        result = pipeline.run_synthesis_iteration_loop(
            customer_profile=_customer_profile(),
            seed_plan=seed,
            sensitivity=_sensitivity(),
            max_iter=4,
            collect=_make_collect_stub(),
            build=build_fail_once,
            architect=_MockArchitect(),  # type: ignore[arg-type]
        )
        # Recovery: seed build + failed rebuild + recovery rebuild >= 3.
        assert build_count >= 3
        assert result.stop_reason != "build_failure_streak"
