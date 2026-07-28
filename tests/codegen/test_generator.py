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
