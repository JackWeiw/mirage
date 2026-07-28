"""Tests for ScaffoldGenerator."""

import json
import pathlib
import tempfile

from codegen.scaffold_gen import ScaffoldGenerator


def test_generate_project_creates_files() -> None:
    gen = ScaffoldGenerator()
    output_dir = pathlib.Path(tempfile.mkdtemp())
    context = {
        "project_name": "test_workload",
        "compile_flags": "-O2 -march=armv8.2-a",
        "dependencies": [{"name": "folly", "version": "2.1.0"}],
        "dep_headers": ["folly/futures/Future.h"],
        "stages": [],
        "extra_sources": [],
        "config": {"thread_count": 4, "qps": 100},
    }
    gen.generate(context, output_dir)
    assert (output_dir / "CMakeLists.txt").exists()
    assert (output_dir / "main.cpp").exists()
    assert (output_dir / "config_loader.h").exists()
    assert (output_dir / "config.json").exists()


def test_generate_cmake_contains_project_name() -> None:
    gen = ScaffoldGenerator()
    output_dir = pathlib.Path(tempfile.mkdtemp())
    context = {
        "project_name": "search_ranking_sim",
        "compile_flags": "-O2",
        "dependencies": [{"name": "folly", "version": "2.1.0"}],
        "dep_headers": [],
        "stages": [],
        "extra_sources": [],
        "config": {},
    }
    gen.generate(context, output_dir)
    cmake_content = (output_dir / "CMakeLists.txt").read_text()
    assert "search_ranking_sim" in cmake_content
    assert "folly" in cmake_content


def test_generate_config_json_is_valid_json() -> None:
    gen = ScaffoldGenerator()
    output_dir = pathlib.Path(tempfile.mkdtemp())
    context = {
        "project_name": "test",
        "compile_flags": "-O2",
        "dependencies": [],
        "dep_headers": [],
        "stages": [],
        "extra_sources": [],
        "config": {"thread_count": 8, "qps": 500},
    }
    gen.generate(context, output_dir)
    config_data = json.loads((output_dir / "config.json").read_text())
    assert config_data["thread_count"] == 8
    assert config_data["qps"] == 500
