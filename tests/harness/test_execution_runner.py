"""Tests for ExecutionRunner."""

import os
import pathlib
import sys
import tempfile

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
        script_path.write_text("echo 'test output'\nexit 0\n")
        os.chmod(script_path, 0o755)
    runner = ExecutionRunner()
    result = runner.validate_run(str(script_path))
    assert result.success is True
    assert "test output" in result.stdout
