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

import subprocess  # noqa: E402
import time  # noqa: E402
from typing import cast  # noqa: E402

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


def numactl_taskset_prefix(cpu_mask: str, numa_node: str) -> list[str]:
    """Build the launch prefix that binds CPU + memory (numactl) then pins cores (taskset).

    numactl wraps taskset so BOTH the cpu node bind and the memory bind apply to the
    binary; taskset then further restricts to cpu_mask within that node. Order matters:
    `numactl --cpunodebind=N --membind=N taskset -c <mask> <binary> ...`
    """
    return [
        "numactl",
        f"--cpunodebind={numa_node}",
        f"--membind={numa_node}",
        "taskset",
        "-c",
        cpu_mask,
    ]


def synthetic_collect(
    binary: str,
    instr: dict[str, object],
    *,
    cfg: CollectionConfig,
    metrics: object,
    project_dir: pathlib.Path,
) -> object:
    """Custom `collect` callable for run_loop_demo: mirrors Pipeline.run_and_collect
    but prepends numactl (NUMA-bound memory) to the launch. Reads the pre-written
    project_dir/config.json (codegen writes it; the runtime tier rewrites it). Returns
    a workload Profile or a RunFailure.

    Mirrors src/harness/pipeline.py run_and_collect's happy path; run failures return
    a RunFailure and the loop's run_failure_streak handles retries.
    """
    from models.results import RunFailure

    config_path = str((project_dir / "config.json").resolve())
    launch_cmd: list[str] = [
        *numactl_taskset_prefix(cfg.cpu_mask, cfg.numa_node),
        str(binary),
        config_path,
    ]
    try:
        proc = subprocess.Popen(
            launch_cmd,
            cwd=str(project_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        return RunFailure(reason=f"binary_launch_failed: {exc}", kind="crash")

    instr_cfg_raw = instr.get("config", {}) if isinstance(instr, dict) else {}
    instr_cfg = cast("dict[str, object]", instr_cfg_raw)
    warmup = int(cast("int", instr_cfg.get("warmup_seconds", cfg.warmup_seconds)))
    time.sleep(warmup)
    if proc.poll() is not None:
        return RunFailure(
            reason=f"workload_exited_during_warmup rc={proc.returncode}",
            kind="crash",
        )

    td_path = project_dir / "topdown.txt"
    pid = int(proc.pid)
    coll = metrics.collect_topdown(  # type: ignore[attr-defined]
        td_path,
        duration=cfg.measurement_seconds,
        interval=cfg.interval_seconds,
        pid=pid,
    )
    if not coll.success or coll.topdown_path is None:
        proc.wait(timeout=cfg.measurement_seconds + 30)
        return RunFailure(reason=coll.error or "collect_failed", kind="collect_fail")
    try:
        proc.wait(timeout=cfg.measurement_seconds + 30)
    except subprocess.TimeoutExpired:
        proc.kill()
        return RunFailure(reason="workload_hang", kind="timeout")
    prof = metrics.parse_topdown_file(pathlib.Path(coll.topdown_path))  # type: ignore[attr-defined]
    if prof.topdown is None:
        return RunFailure(reason="no_topdown_l1_lines", kind="collect_fail")
    return prof
