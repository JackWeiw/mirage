"""Tests for ExecutionRunner."""

import os
import pathlib
import subprocess
import sys
import tempfile
import time
from typing import Any

from harness.execution_runner import ExecutionRunner
from harness.run_config import RunConfig
from models.results import ExecutionResult


def test_run_config_defaults() -> None:
    cfg = RunConfig()
    assert cfg.thread_count == 4
    assert cfg.warmup_seconds == 30


def test_run_config_custom() -> None:
    cfg = RunConfig(thread_count=16, qps=500, warmup_seconds=10)
    assert cfg.thread_count == 16
    assert cfg.qps == 500


def test_execution_result_success() -> None:
    result = ExecutionResult(success=True, stdout="done", stderr="", exit_code=0)
    assert result.success is True


def test_validate_run_missing_binary() -> None:
    runner = ExecutionRunner()
    result = runner.validate_run("/nonexistent/binary")
    assert result.success is False


def test_validate_run_with_echo_script() -> None:
    script_dir = pathlib.Path(tempfile.mkdtemp())
    if sys.platform == "win32":
        # Windows: use a .bat file
        script_path = script_dir / "test_binary.bat"
        script_path.write_text("@echo test output\r\n@exit /b 0\r\n")
    else:
        script_path = script_dir / "test_binary.sh"
        script_path.write_text("#!/bin/sh\necho 'test output'\nexit 0\n")
        os.chmod(script_path, 0o755)
    runner = ExecutionRunner()
    result = runner.validate_run(str(script_path))
    assert result.success is True
    assert "test output" in result.stdout


# -- ExecutionResult.duration_seconds: populated on every return path (#2) -----


def _monotonic_pair(start: float, end: float) -> Any:
    """time.monotonic stub yielding start then end (exactly 2 calls per run)."""
    ticks = iter([start, end])
    return lambda: next(ticks)


def test_run_sets_duration_seconds_on_success(monkeypatch: Any) -> None:
    """run() populates duration_seconds with the wall-clock subprocess time,
    not the dead 0.0 default."""
    monkeypatch.setattr(time, "monotonic", _monotonic_pair(100.0, 100.5))

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = ExecutionRunner().run("/fake/binary")
    assert result.success is True
    assert result.duration_seconds == 0.5


def test_run_sets_duration_seconds_on_timeout(monkeypatch: Any) -> None:
    """run() still populates duration_seconds on the TimeoutExpired path."""
    monkeypatch.setattr(time, "monotonic", _monotonic_pair(200.0, 200.25))

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = ExecutionRunner().run("/fake/binary")
    assert result.success is False
    assert result.duration_seconds == 0.25


def test_validate_run_sets_duration_seconds_on_success(monkeypatch: Any) -> None:
    """validate_run() populates duration_seconds on its success path."""
    monkeypatch.setattr(time, "monotonic", _monotonic_pair(300.0, 301.0))

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="x", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = ExecutionRunner().validate_run("/fake/binary")
    assert result.success is True
    assert result.duration_seconds == 1.0
