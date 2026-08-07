"""Tests for BuildRunner."""

import pathlib
import tempfile

from harness.build_runner import BuildRunner


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
