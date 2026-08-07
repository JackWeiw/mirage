"""Execution Runner — run workload binary with warmup and measurement phases."""

import subprocess

from harness.run_config import RunConfig
from models.results import ExecutionResult
from observability.logging import get_logger

logger = get_logger("execution_runner")


class ExecutionRunner:
    """Run a workload binary with warmup + measurement phases.

    Args:
        default_timeout: Maximum total execution time in seconds.
    """

    def __init__(self, default_timeout: int = 300) -> None:
        self.default_timeout = default_timeout

    def run(self, binary_path: str, run_config: RunConfig | None = None) -> ExecutionResult:
        """Execute a workload binary.

        Args:
            binary_path: Path to the compiled workload binary.
            run_config: Runtime configuration.

        Returns:
            ExecutionResult with success status, output, and exit code.
        """
        if run_config is None:
            run_config = RunConfig()

        total_timeout = run_config.warmup_seconds + run_config.measurement_seconds + 30
        cmd = [binary_path, run_config.config_path]

        logger.info("running_workload", cmd=cmd, timeout=total_timeout)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=total_timeout)
            return ExecutionResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            logger.error("workload_timeout", timeout=total_timeout)
            return ExecutionResult(success=False, stderr=f"Timeout after {total_timeout}s")
        except FileNotFoundError:
            return ExecutionResult(success=False, stderr=f"Binary not found: {binary_path}")

    def validate_run(self, binary_path: str, timeout: int = 5) -> ExecutionResult:
        """Short validation run to check if the binary can start.

        Args:
            binary_path: Path to the workload binary.
            timeout: Short timeout in seconds.

        Returns:
            ExecutionResult from the short run.
        """
        cmd = [binary_path]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return ExecutionResult(
                success=bool(result.stdout or result.stderr),
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            # Binary started but didn't finish in timeout — OK for validation
            return ExecutionResult(success=True, stdout="(timeout — binary started)")
        except FileNotFoundError:
            return ExecutionResult(success=False, stderr=f"Binary not found: {binary_path}")
