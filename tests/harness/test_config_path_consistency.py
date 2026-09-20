"""Tests for config.json path consistency (Known Issue #4).

codegen writes ``<project>/config.json``, but the binary reads
``<project>/build/config.json`` (run_and_collect derives pdir = binary.parent =
build/). ``build_workload_result`` copies the seed config into the build dir
after a successful build so the iter-1 config isn't silently lost to
config_loader's baked defaults. The runtime-tier rewrite already writes
build/config.json for iter 2+.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from config.framework_config import FrameworkConfig
from harness.pipeline import Pipeline
from models.results import BuildResult

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Callable


def _stub_build(binary: pathlib.Path) -> Callable[[pathlib.Path], BuildResult]:
    """BuildRunner.build replacement: skip cmake/make, return the fake binary."""

    def _build(_project_dir: pathlib.Path) -> BuildResult:
        return BuildResult(success=True, binary_path=str(binary))

    return _build


def test_build_workload_result_copies_seed_config_to_build_dir(
    tmp_path: pathlib.Path, monkeypatch: Any
) -> None:
    """#4: after a successful build the codegen config.json is copied into the
    build dir (where the binary reads it) so iter 1 isn't stuck on baked defaults."""
    project = tmp_path / "project"
    build = project / "build"
    build.mkdir(parents=True)
    binary = build / "workload"
    binary.write_text("")  # fake binary; build/ pre-exists so the copy lands
    seed_cfg = {"thread_count": 8, "compute_ratio": 0.8}
    (project / "config.json").write_text(json.dumps(seed_cfg))

    pipeline = Pipeline(output_base_dir=tmp_path, config=FrameworkConfig())
    monkeypatch.setattr(pipeline.build_runner, "build", _stub_build(binary))

    result = pipeline.build_workload_result(project)
    assert result.success
    copied = json.loads((build / "config.json").read_text())
    assert copied == seed_cfg


def test_build_workload_result_no_crash_without_codegen_config(
    tmp_path: pathlib.Path, monkeypatch: Any
) -> None:
    """#4: a missing codegen config.json is a guarded no-op (no crash, no copy)."""
    project = tmp_path / "project"
    build = project / "build"
    build.mkdir(parents=True)
    binary = build / "workload"
    binary.write_text("")

    pipeline = Pipeline(output_base_dir=tmp_path, config=FrameworkConfig())
    monkeypatch.setattr(pipeline.build_runner, "build", _stub_build(binary))

    result = pipeline.build_workload_result(project)
    assert result.success
    assert not (build / "config.json").exists()
