"""Integration tests for the full pipeline."""

import os
import pathlib

import pytest

from agent.agent_core import AgentCore
from config.framework_config import AgentConfig
from harness.pipeline import Pipeline
from ingestion.flamegraph_parser import FlamegraphParser
from ingestion.topdown_parser import TopdownParser
from profile.profile_schema import Profile, ProfileMetadata, TopdownL1

EXAMPLES_DIR = (
    pathlib.Path(__file__).parent.parent.parent / "examples" / "search_ranking" / "customer_data"
)


def test_pipeline_ingest_and_generate() -> None:
    """Full pipeline with Agent (requires ANTHROPIC_API_KEY)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip(reason="ANTHROPIC_API_KEY not set")
    output_dir = pathlib.Path(__file__).parent.parent.parent / "test_output"
    pipeline = Pipeline(output_base_dir=output_dir)
    profile = pipeline.ingest_customer_data(
        flamegraph_path=EXAMPLES_DIR / "flamegraph_folded.txt",
        topdown_path=EXAMPLES_DIR / "topdown.json",
        customer_name="search_ranking",
        metadata={
            "date": "2026-07-27",
            "neoverse_core": "N2",
            "business_logic": "Search ranking service",
        },
    )
    assert profile.metadata.customer == "search_ranking"
    assert len(profile.hotspots) > 0
    assert profile.topdown is not None


def test_pipeline_ingest_only() -> None:
    """Test ingestion without Agent (no API key needed)."""
    fg_parser = FlamegraphParser()
    td_parser = TopdownParser()

    hotspots = fg_parser.parse_folded(EXAMPLES_DIR / "flamegraph_folded.txt")
    td_profile = td_parser.parse_json(EXAMPLES_DIR / "topdown.json")

    profile = Profile(
        metadata=ProfileMetadata(customer="search_ranking", date="2026-07-27"),
        hotspots=hotspots,
        topdown=td_profile.topdown,
        topdown_l2=td_profile.topdown_l2,
        memory=td_profile.memory,
        business_logic="Search ranking service",
    )

    assert len(profile.hotspots) > 0
    assert profile.topdown is not None
    assert profile.topdown.backend_bound == 55.0


def test_pipeline_local_only_with_manual_instruction() -> None:
    """Test Pipeline in local-only mode with a manually provided instruction."""
    output_dir = pathlib.Path(__file__).parent.parent.parent / "test_output"

    # Create Pipeline with no Agent (local-only mode)
    no_agent = AgentCore(config=AgentConfig(api_key=None))
    pipeline = Pipeline(output_base_dir=output_dir, agent=no_agent)

    instruction = {
        "project_name": "test_local_workload",
        "compile_flags": "-O2",
        "dependencies": [],
        "dep_headers": [],
        "stages": [
            {
                "stage_name": "calc",
                "implementation_strategy": "compute_synthesis",
                "strategies": [
                    {
                        "strategy": "compute_synthesis",
                        "synthesis_config": {
                            "compute_type": "hash",
                            "iterations": 50,
                        },
                    }
                ],
            },
        ],
        "config": {"thread_count": 4, "qps": 100},
    }

    customer_profile = Profile(
        metadata=ProfileMetadata(customer="test", date="2026-07-27"),
        topdown=TopdownL1(
            frontend_bound=25.0,
            backend_bound=40.0,
            bad_speculation=10.0,
            retiring=25.0,
        ),
    )

    project_dir = pipeline.generate_workload(customer_profile, instruction=instruction)
    assert (project_dir / "CMakeLists.txt").exists()
    assert (project_dir / "main.cpp").exists()
    assert (project_dir / "config_loader.h").exists()
    assert (project_dir / "calc.h").exists()


def test_pipeline_ingest_customer_data() -> None:
    """Test the Pipeline.ingest_customer_data method directly."""
    output_dir = pathlib.Path(__file__).parent.parent.parent / "test_output"

    no_agent = AgentCore(config=AgentConfig(api_key=None))
    pipeline = Pipeline(output_base_dir=output_dir, agent=no_agent)

    profile = pipeline.ingest_customer_data(
        flamegraph_path=EXAMPLES_DIR / "flamegraph_folded.txt",
        topdown_path=EXAMPLES_DIR / "topdown.json",
        customer_name="search_ranking",
        metadata={
            "date": "2026-07-27",
            "neoverse_core": "N2",
            "business_logic": "Search ranking service",
        },
    )

    assert profile.metadata.customer == "search_ranking"
    assert profile.metadata.date == "2026-07-27"
    assert profile.metadata.neoverse_core == "N2"
    assert len(profile.hotspots) > 0
    assert profile.topdown is not None
    assert profile.topdown.backend_bound == 55.0
    assert profile.topdown_l2 is not None
    assert profile.memory is not None
    assert profile.memory.bandwidth_gbps == 48.5

    # Verify profile was saved to store
    saved = pipeline.profile_store.load("search_ranking_profile")
    assert saved.metadata.customer == "search_ranking"


def test_pipeline_compare_results() -> None:
    """Test the Pipeline.compare_results method."""
    output_dir = pathlib.Path(__file__).parent.parent.parent / "test_output"

    no_agent = AgentCore(config=AgentConfig(api_key=None))
    pipeline = Pipeline(output_base_dir=output_dir, agent=no_agent)

    customer_profile = pipeline.ingest_customer_data(
        flamegraph_path=EXAMPLES_DIR / "flamegraph_folded.txt",
        topdown_path=EXAMPLES_DIR / "topdown.json",
        customer_name="search_ranking",
        metadata={"date": "2026-07-27"},
    )

    # Create a synthetic workload profile (close to customer)
    from profile.profile_schema import MemoryProfile

    workload_profile = Profile(
        metadata=ProfileMetadata(customer="workload", date="2026-07-27"),
        topdown=TopdownL1(
            frontend_bound=19.0,
            backend_bound=54.0,
            bad_speculation=7.0,
            retiring=20.0,
        ),
        memory=MemoryProfile(bandwidth_gbps=48.0),
    )

    report = pipeline.compare_results(customer_profile, workload_profile, iteration=0)

    assert "convergence" in report
    assert "topdown_l1" in report
    assert "hotspot_coverage" in report
    assert isinstance(report["convergence"]["converged"], bool)

    # Verify iteration history was updated
    assert len(pipeline.history.records) == 1
    assert pipeline.history.records[0].iteration == 0


def test_pipeline_run_and_compare_structural_alignment(tmp_path: pathlib.Path) -> None:
    """run_and_compare emits a structural-alignment report (no build/run needed)."""
    from codegen.call_tree import CallTreeBuilder

    no_agent = AgentCore(config=AgentConfig(api_key=None))
    pipeline = Pipeline(output_base_dir=tmp_path, agent=no_agent)
    desc = CallTreeBuilder().build(
        [(["main", "Svc::process", "folly::X"], 100)], profile=None, project_name="t"
    )
    project_dir = pipeline.generate_workload_from_descriptor(desc)
    assert (project_dir / "service.cpp").exists()
    assert (project_dir / "main.cpp").exists()

    workload_stacks = [(["main", "Svc::process", "folly::X"], 100)]
    report = pipeline.run_and_compare(
        customer_stacks=[(["main", "Svc::process", "folly::X"], 100)],
        workload_stacks=workload_stacks,
    )
    assert "structural_alignment" in report
    assert report["structural_alignment"]["overall_overlap_pct"] == 100.0
    assert report["structural_alignment"]["trunk_present"] is True
