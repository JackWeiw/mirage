"""Phase A: the dormant modular codegen path emits a compilable-shaped project
from a customer call_tree."""

from __future__ import annotations

from typing import TYPE_CHECKING

from models.results import PipelineResult
from profile.profile_schema import CallTreeNode, Profile, ProfileMetadata

if TYPE_CHECKING:
    import pathlib


def _profile_with_call_tree() -> Profile:
    """main -> ns_a::foo() -> ns_b::bar()  (cross-namespace call -> bar is public,
    ns_a depends_on ns_b; three modules: main, a, b)."""
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


def test_generate_workload_from_module_graph_emits_project(tmp_path: pathlib.Path) -> None:
    """The dormant modular path, wired into Pipeline, emits a compilable-shaped
    project (module .h/.cpp topologically + CMakeLists + main + config) from a
    Profile with call_tree -- no LLM, no instruction dict."""
    from harness.pipeline import Pipeline

    pipeline = Pipeline(output_base_dir=tmp_path / "base")
    profile = _profile_with_call_tree()
    out = pipeline.generate_workload_from_module_graph(profile, tmp_path / "project")

    assert out == tmp_path / "project"
    # module headers + impls emitted (modules: main, ns_a, ns_b)
    assert (tmp_path / "project" / "main.h").exists()
    assert (tmp_path / "project" / "ns_a.h").exists()
    assert (tmp_path / "project" / "ns_b.h").exists()
    assert (tmp_path / "project" / "ns_a.cpp").exists()
    assert (tmp_path / "project" / "ns_b.cpp").exists()
    # scaffold
    assert (tmp_path / "project" / "CMakeLists.txt").exists()
    assert (tmp_path / "project" / "main.cpp").exists()
    assert (tmp_path / "project" / "config_loader.h").exists()
    assert (tmp_path / "project" / "config.json").exists()
    # cross-namespace dep wired: ns_a.h includes ns_b.h (ns_a depends_on ns_b)
    a_h = (tmp_path / "project" / "ns_a.h").read_text()
    assert '"ns_b.h"' in a_h
    # CMakeLists references the module impl sources
    cmake = (tmp_path / "project" / "CMakeLists.txt").read_text()
    assert "ns_a.cpp" in cmake and "ns_b.cpp" in cmake


def test_run_modular_pipeline_ingest_to_build_shape(tmp_path: pathlib.Path) -> None:
    """run_modular_pipeline mirrors run_full_pipeline but uses the module-graph
    path (no instruction, no agent). Build will fail on Windows (no ARM
    toolchain) -- Phase A only asserts the project was generated + the result
    is a PipelineResult."""
    from harness.pipeline import Pipeline

    pipeline = Pipeline(output_base_dir=tmp_path / "base")
    profile = _profile_with_call_tree()
    result = pipeline.run_modular_pipeline(
        customer_profile=profile, output_dir=tmp_path / "project"
    )
    # Project was generated regardless of build outcome.
    assert (tmp_path / "project" / "CMakeLists.txt").exists()
    assert isinstance(result, PipelineResult)


def test_design_synthesis_plan_offline_returns_deterministic(
    tmp_path: pathlib.Path,
) -> None:
    """Pipeline.design_synthesis_plan wires the ArchitectAgent; offline (no api_key)
    -> source='deterministic' with one task per function. Uses FrameworkConfig.defaults()
    (env-free) so the assertion is LLM-path-independent -- unlike Phase A's
    LLM-agnostic tests which can use bare Pipeline() (from_env)."""
    from config.framework_config import FrameworkConfig
    from harness.pipeline import Pipeline

    pipeline = Pipeline(output_base_dir=tmp_path / "base", config=FrameworkConfig.defaults())
    profile = _profile_with_call_tree()
    plan = pipeline.design_synthesis_plan(profile)
    assert plan.source == "deterministic"
    mod_names = {m.name for m in plan.module_graph.modules}
    assert {"main", "ns_a", "ns_b"}.issubset(mod_names)
    assert len(plan.tasks) >= 2
    assert all(t.status == "pending" for t in plan.tasks)


def test_run_synthesis_pipeline_offline_shape(tmp_path: pathlib.Path) -> None:
    """run_synthesis_pipeline: architect (offline) -> deterministic plan; orchestrator
    (offline) -> no patch; build attempted. Offline shape == run_modular_pipeline shape
    (project generated + PipelineResult). Build fails on Windows (no ARM toolchain)."""
    from config.framework_config import FrameworkConfig
    from harness.pipeline import Pipeline

    pipeline = Pipeline(output_base_dir=tmp_path / "base", config=FrameworkConfig.defaults())
    profile = _profile_with_call_tree()
    result = pipeline.run_synthesis_pipeline(profile, tmp_path / "project")
    assert (tmp_path / "project" / "CMakeLists.txt").exists()
    assert isinstance(result, PipelineResult)
