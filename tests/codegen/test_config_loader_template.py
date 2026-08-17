"""Tests for the nlohmann-free config_loader.h template."""

import pathlib
import tempfile
from typing import Any

from codegen.scaffold_gen import ScaffoldGenerator


def _render_config_loader(config: dict[str, Any]) -> str:
    gen = ScaffoldGenerator()
    output_dir = pathlib.Path(tempfile.mkdtemp())
    context = {
        "project_name": "sim",
        "compile_flags": "-O2",
        "dependencies": [],
        "dep_headers": [],
        "stages": [],
        "extra_sources": [],
        "config": config,
        "burst": 20,
    }
    gen.generate(context, output_dir)
    return (output_dir / "config_loader.h").read_text()


def test_config_loader_has_no_nlohmann_dependency() -> None:
    text = _render_config_loader({})
    # No nlohmann #include and no nlohmann:: namespace usage (the dependency is
    # gone). The bare word "nlohmann" may appear in a comment explaining why the
    # dependency was removed -- that is documentation, not a dependency.
    assert "#include <nlohmann" not in text
    assert "nlohmann::" not in text


def test_config_loader_has_struct_and_reader() -> None:
    text = _render_config_loader({})
    assert "struct RunConfig" in text
    assert "load_config" in text
    assert "config_path" in text


def test_config_loader_bakes_defaults_from_config() -> None:
    text = _render_config_loader({"thread_count": 8, "compute_ratio": 0.8})
    assert "8" in text
    assert "0.8" in text


def test_config_loader_bakes_default_fallbacks_when_key_absent() -> None:
    text = _render_config_loader({})
    for token in (
        "thread_count",
        "qps",
        "warmup_seconds",
        "measurement_seconds",
        "compute_ratio",
        "memory_ratio",
    ):
        assert token in text
