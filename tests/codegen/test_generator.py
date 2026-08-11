"""Tests for WorkloadGenerator orchestrator."""

import json
import pathlib
import tempfile

from codegen.generator import WorkloadGenerator


def test_workload_generator_full_project() -> None:
    gen = WorkloadGenerator()
    output_dir = pathlib.Path(tempfile.mkdtemp())
    instruction = {
        "project_name": "search_ranking_sim",
        "compile_flags": "-O2 -march=armv8.2-a",
        "dependencies": [{"name": "folly", "version": "2.1.0"}],
        "dep_headers": ["folly/futures/Future.h"],
        "stages": [
            {
                "stage_name": "feature_calc",
                "implementation_strategy": "compute_synthesis",
                "strategies": [
                    {
                        "strategy": "compute_synthesis",
                        "synthesis_config": {"compute_type": "hash", "iterations": 100},
                    }
                ],
            },
            {
                "stage_name": "data_lookup",
                "implementation_strategy": "memory_synthesis",
                "strategies": [
                    {
                        "strategy": "memory_synthesis",
                        "synthesis_config": {
                            "access_pattern": "random",
                            "working_set_mb": 32,
                        },
                    }
                ],
            },
        ],
        "config": {"thread_count": 8, "qps": 500, "warmup_seconds": 30, "measurement_seconds": 60},
    }
    result_dir = gen.generate(instruction, output_dir)
    assert (result_dir / "CMakeLists.txt").exists()
    assert (result_dir / "main.cpp").exists()
    assert (result_dir / "config_loader.h").exists()
    assert (result_dir / "config.json").exists()
    assert (result_dir / "feature_calc.h").exists()
    assert (result_dir / "data_lookup.h").exists()
    config = json.loads((result_dir / "config.json").read_text())
    assert config["thread_count"] == 8
    assert config["qps"] == 500


def test_generate_from_descriptor_produces_skeleton_project(tmp_path: pathlib.Path) -> None:
    from codegen.call_tree import CallTreeBuilder
    from codegen.catalog import OpenSourceAPICatalog

    stacks = [
        (
            ["main", "Svc::process", "StageA::run", "folly::futures::detail::FutureImpl::then"],
            100,
        ),
        (["main", "Svc::process", "StageA::run", "Customer::hashFeature"], 50),
    ]
    desc = CallTreeBuilder(catalog=OpenSourceAPICatalog()).build(
        stacks, profile=None, project_name="t"
    )
    WorkloadGenerator().generate_from_descriptor(desc, tmp_path)
    assert (tmp_path / "service.h").exists()
    assert (tmp_path / "service.cpp").exists()
    assert (tmp_path / "main.cpp").exists()
    assert (tmp_path / "CMakeLists.txt").exists()
    assert (tmp_path / "config.json").exists()
    assert any(p.name.endswith("_synth.h") for p in tmp_path.iterdir())
    cmake = (tmp_path / "CMakeLists.txt").read_text()
    assert "service.cpp" in cmake
    assert "folly" in cmake


def test_custom_leaf_directly_under_service_is_not_dropped(tmp_path: pathlib.Path) -> None:
    # Regression: a custom leaf with no wrapper stage (main;process;Customer::work)
    # must not produce an empty stage function - its synth header is generated
    # and the stage calls it.
    from codegen.call_tree import CallTreeBuilder

    stacks = [(["main", "Svc::process", "Customer::workload"], 100)]
    desc = CallTreeBuilder().build(stacks, profile=None, project_name="t")
    WorkloadGenerator().generate_from_descriptor(desc, tmp_path)
    service_cpp = (tmp_path / "service.cpp").read_text()
    assert any(p.name.endswith("_synth.h") for p in tmp_path.iterdir())
    assert "_custom_synth();" in service_cpp
