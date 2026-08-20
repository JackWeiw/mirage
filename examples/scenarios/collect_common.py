"""Shared collection utilities for the steering-validation demos.

Imported by both collect_reference.py (reference side) and run_loop_demo.py
(synthetic side) so both captures use identical collection conditions from one
collection.yaml. Mirrors steerability_spike's sys.path-prepend convention for
reaching src/ packages (harness, config, ...).
"""

import pathlib
import sys


def _ensure_src_on_path() -> None:
    """Add src/ to sys.path if not already present."""
    src = pathlib.Path(__file__).resolve().parents[2] / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


_ensure_src_on_path()

import yaml  # noqa: E402


class CollectionConfig:
    """Typed view of a scenario's collection.yaml (shared by both sides)."""

    def __init__(
        self,
        duration_seconds: int,
        interval_seconds: int,
        perf_freq: int,
        cpu_mask: str,
        numa_node: str,
        per_worker_buffer_mb: int,
        warmup_seconds: int,
        measurement_seconds: int,
        llc_miss_floor_pct: float = 0.0,
    ) -> None:
        self.duration_seconds = duration_seconds
        self.interval_seconds = interval_seconds
        self.perf_freq = perf_freq
        self.cpu_mask = cpu_mask
        self.numa_node = numa_node
        self.per_worker_buffer_mb = per_worker_buffer_mb
        self.warmup_seconds = warmup_seconds
        self.measurement_seconds = measurement_seconds
        self.llc_miss_floor_pct = llc_miss_floor_pct

    @classmethod
    def from_yaml(cls, path: pathlib.Path) -> "CollectionConfig":
        data = yaml.safe_load(path.read_text())
        return cls(
            duration_seconds=int(data["duration_seconds"]),
            interval_seconds=int(data["interval_seconds"]),
            perf_freq=int(data["perf_freq"]),
            cpu_mask=str(data["cpu_mask"]),
            numa_node=str(data["numa_node"]),
            per_worker_buffer_mb=int(data["per_worker_buffer_mb"]),
            warmup_seconds=int(data["warmup_seconds"]),
            measurement_seconds=int(data["measurement_seconds"]),
            llc_miss_floor_pct=float(data.get("llc_miss_floor_pct", 0.0)),
        )
