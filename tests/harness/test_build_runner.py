"""Tests for BuildRunner."""

import pathlib
import subprocess
import tempfile
from typing import Any

from harness.build_runner import BuildRunner, _locate_binary, _read_cmake_target


def test_build_result_success() -> None:
    from models.results import BuildResult

    result = BuildResult(success=True, stdout="built", stderr="", binary_path="/tmp/workload")
    assert result.success is True
    assert result.binary_path is not None


def test_build_result_failure() -> None:
    from models.results import BuildResult

    result = BuildResult(success=False, stderr="cmake failed")
    assert result.success is False
    assert result.binary_path is None


def test_build_missing_cmake() -> None:
    runner = BuildRunner(cmake_path="/nonexistent/cmake")
    project_dir = pathlib.Path(tempfile.mkdtemp())
    result = runner.build(project_dir)
    assert result.success is False
    assert "cmake not found" in result.stderr or "cmake" in result.stderr.lower()


def _make_fake_build_tree(project_dir: pathlib.Path, target: str | None) -> pathlib.Path:
    """Synthesize a CMake build tree without running cmake/make.

    build_dir gets: Makefile, CMakeCache.txt, a CMakeFiles/ artifact, and the
    <target> binary (when given). project_dir gets a CMakeLists.txt whose
    add_executable line is present only when target is not None.
    """
    build_dir = project_dir / "build"
    build_dir.mkdir(parents=True)
    (build_dir / "Makefile").write_text("all:\n")
    (build_dir / "CMakeCache.txt").write_text("cache")
    (build_dir / "CMakeFiles").mkdir()
    (build_dir / "CMakeFiles" / "x.o").write_text("")
    if target is not None:
        (build_dir / target).write_text("#!/bin/sh\n")  # fake binary
    cmake = project_dir / "CMakeLists.txt"
    if target is not None:
        cmake.write_text(f"project({target})\nadd_executable({target} main.cpp)\n")
    else:
        cmake.write_text("project(no_target)\n")
    return build_dir


def test_read_cmake_target_extracts_name(tmp_path: pathlib.Path) -> None:
    (tmp_path / "CMakeLists.txt").write_text(
        "project(workload_sim)\nadd_executable(workload_sim main.cpp)\n"
    )
    assert _read_cmake_target(tmp_path) == "workload_sim"


def test_read_cmake_target_none_when_missing(tmp_path: pathlib.Path) -> None:
    assert _read_cmake_target(tmp_path) is None
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    assert _read_cmake_target(tmp_path) is None


def test_locate_binary_prefers_cmake_target(tmp_path: pathlib.Path) -> None:
    # Regression: rglob used to return the Makefile (no suffix -> not
    # blacklisted). The CMake-target path resolves the binary directly.
    build_dir = _make_fake_build_tree(tmp_path, "workload_sim")
    binary = _locate_binary(build_dir, tmp_path)
    assert binary is not None
    assert pathlib.Path(binary).name == "workload_sim"
    assert pathlib.Path(binary).parent == build_dir


def test_locate_binary_skips_makefile_when_no_target(tmp_path: pathlib.Path) -> None:
    # CMakeLists without add_executable -> primary path misses, fallback runs;
    # the Makefile must be skipped by name, not returned.
    build_dir = _make_fake_build_tree(tmp_path, "workload_sim")
    (tmp_path / "CMakeLists.txt").write_text("project(no_target)\n")
    binary = _locate_binary(build_dir, tmp_path)
    assert binary is not None
    assert pathlib.Path(binary).name == "workload_sim"


def test_locate_binary_returns_none_when_no_binary(tmp_path: pathlib.Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "Makefile").write_text("all:\n")
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    assert _locate_binary(build_dir, tmp_path) is None


# -- BuildResult.duration_seconds: populated on every return path (#48) -----


def test_build_sets_duration_seconds_on_success(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    """A successful build populates duration_seconds with a real wall-clock
    time instead of the dead 0.0 default."""

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    (tmp_path / "CMakeLists.txt").write_text(
        "project(workload_sim)\nadd_executable(workload_sim main.cpp)\n"
    )
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "workload_sim").write_text("#!/bin/sh\n")  # fake binary

    runner = BuildRunner()
    result = runner.build(tmp_path)
    assert result.success is True
    assert result.binary_path is not None
    assert pathlib.Path(result.binary_path).name == "workload_sim"
    assert isinstance(result.duration_seconds, float)
    assert result.duration_seconds >= 0.0


def test_build_sets_duration_seconds_on_failure(tmp_path: pathlib.Path) -> None:
    """A failed build (cmake not found) still populates duration_seconds."""
    runner = BuildRunner(cmake_path="/nonexistent/cmake")
    result = runner.build(tmp_path)
    assert result.success is False
    assert isinstance(result.duration_seconds, float)
    assert result.duration_seconds >= 0.0


def test_build_sets_duration_seconds_on_nonzero_rc(
    tmp_path: pathlib.Path, monkeypatch: Any
) -> None:
    """A build that fails mid-step (cmake returns non-zero) still populates
    duration_seconds. Pins the non-zero-rc return path, distinct from the
    cmake-not-found and success paths."""

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = BuildRunner()
    result = runner.build(tmp_path)
    assert result.success is False
    assert isinstance(result.duration_seconds, float)
    assert result.duration_seconds >= 0.0
