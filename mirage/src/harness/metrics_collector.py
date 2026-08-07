"""Metrics Collector — collect and parse Topdown, flamegraph, and memory data."""

import pathlib
import subprocess

from ingestion.flamegraph_parser import FlamegraphParser
from ingestion.topdown_parser import TopdownParser
from models.results import CollectionResult
from observability.logging import get_logger
from profile.profile_schema import HotspotFunction, Profile

logger = get_logger("metrics_collector")


class MetricsCollector:
    """Collect performance metrics from a workload run on the target machine.

    Args:
        devkit_cmd: Path to devkit command. None means devkit is not configured.
        perf_cmd: Path to perf command.
    """

    def __init__(self, devkit_cmd: str | None = None, perf_cmd: str = "perf") -> None:
        self.devkit_cmd = devkit_cmd
        self.perf_cmd = perf_cmd
        self.topdown_parser = TopdownParser()
        self.flamegraph_parser = FlamegraphParser()

    def collect_topdown(self, output_path: pathlib.Path, duration: int = 60) -> CollectionResult:
        """Collect Topdown data using devkit."""
        if self.devkit_cmd is None:
            logger.warning("devkit_not_configured")
            return CollectionResult(success=False, error="devkit_cmd not configured")

        cmd = [
            self.devkit_cmd,
            "topdown",
            "--duration",
            str(duration),
            "--output",
            str(output_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 30)
            if result.returncode != 0:
                return CollectionResult(success=False, error=result.stderr)
            return CollectionResult(success=True, topdown_path=str(output_path))
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return CollectionResult(success=False, error=str(e))

    def parse_topdown_file(self, filepath: pathlib.Path) -> Profile:
        """Parse a previously collected Topdown JSON file."""
        return self.topdown_parser.parse_json(filepath)

    def parse_flamegraph_file(self, filepath: pathlib.Path) -> list[HotspotFunction]:
        """Parse a previously collected flamegraph folded file."""
        return self.flamegraph_parser.parse_folded(filepath)
