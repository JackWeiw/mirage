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

from observability.logging import get_logger  # noqa: E402

logger = get_logger("collect_common")


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

    # Resolve the binary to an absolute path BEFORE building launch_cmd: the
    # loop passes a path rooted at the repo root (e.g.
    # 'run_out/generated_workload/build/memory_bound_seed'), but we run the
    # subprocess with cwd=project_dir (the scaffold dir), so a relative binary
    # path would resolve to <scaffold>/<relative> -> ENOENT -> rc=127. Mirrors
    # pipeline.run_and_collect (binary = str(Path(binary_path).resolve())).
    binary_abs = str(pathlib.Path(binary).resolve())
    config_path = str((project_dir / "config.json").resolve())
    launch_cmd: list[str] = [
        *numactl_taskset_prefix(cfg.cpu_mask, cfg.numa_node),
        binary_abs,
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
    # Time the collect from the INSTRUCTION's config (what the binary actually
    # runs), not collection.yaml -- the two can diverge and a mismatch either
    # samples the wrong window or sizes the reap budget wrong.
    warmup = int(cast("int", instr_cfg.get("warmup_seconds", cfg.warmup_seconds)))
    measurement = int(cast("int", instr_cfg.get("measurement_seconds", cfg.measurement_seconds)))
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
        duration=measurement,
        interval=cfg.interval_seconds,
        pid=pid,
    )
    if not coll.success or coll.topdown_path is None:
        try:
            proc.wait(timeout=measurement + 30)
        except subprocess.TimeoutExpired:
            proc.kill()
        return RunFailure(reason=coll.error or "collect_failed", kind="collect_fail")
    # Collect SUCCEEDED -- the topdown was captured over the measurement window.
    # The binary may linger after measurement (threadpool teardown / a runtime
    # slightly over budget); reap it with a pad and, if it hangs, kill it and
    # USE the data we already captured. A post-measurement cleanup hang does NOT
    # invalidate the sampled profile -- the old code discarded it (workload_hang)
    # and caused false run_failure_streak.
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    prof = metrics.parse_topdown_file(pathlib.Path(coll.topdown_path))  # type: ignore[attr-defined]
    if prof.topdown is None:
        return RunFailure(reason="no_topdown_l1_lines", kind="collect_fail")
    return prof


# ---------------------------------------------------------------------------
# Reference-side capture helpers (used by collect_reference.py entry points)
# ---------------------------------------------------------------------------

_MARKER = "__MEASUREMENT_WINDOW_START__"


def _wait_for_marker(proc: object, timeout: int) -> bool:
    """Block until the binary prints the steady-state marker; False on timeout/early exit."""
    deadline = time.monotonic() + timeout
    stdout = getattr(proc, "stdout", None)
    if stdout is None:
        return False
    for line in stdout:
        if _MARKER in line:
            return True
        if time.monotonic() > deadline:
            return False
    return False  # process exited before the marker


def _extract_perf_value(text: str, event: str) -> int:
    """Extract the numeric counter value for `event` from perf-stat output."""
    for line in text.splitlines():
        if event in line:
            for p in line.split():
                p = p.replace(",", "")
                if p.isdigit():
                    return int(p)
    return 0


def _llc_miss_rate(binary: str, cfg: "CollectionConfig", project_dir: pathlib.Path) -> float:
    """cache-misses / cache-references over a short perf-stat window (memory_bound gate)."""
    try:
        out = subprocess.run(
            [
                "perf",
                "stat",
                "-e",
                "cache-misses,cache-references",
                "--",
                *numactl_taskset_prefix(cfg.cpu_mask, cfg.numa_node),
                binary,
                str(project_dir / "config.json"),
            ],
            capture_output=True,
            text=True,
            timeout=cfg.measurement_seconds + 10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 100.0  # perf stat unavailable -> don't block the capture
    text = out.stderr + out.stdout
    misses = _extract_perf_value(text, "cache-misses")
    refs = _extract_perf_value(text, "cache-references")
    if refs == 0:
        return 100.0
    return (misses / refs) * 100.0


def run_reference_capture(
    binary: str,
    scenario_dir: pathlib.Path,
    devkit_cmd: str | None = None,
    *,
    cfg: "CollectionConfig | None" = None,
    metrics: object | None = None,
    flamegraph_dir: str | None = "FlameGraph",
) -> int:
    """Reference-side capture: numactl+taskset launch, marker-gated collection,
    LLC-miss gate (memory_bound), writes topdown.json + flamegraph.svg (non-fatal).

    Defaults `cfg`/`metrics` from `scenario_dir`/`devkit_cmd` when not passed, so
    the entry points stay thin but tests can inject fakes. `flamegraph_dir` is the
    dir holding `stackcollapse-perf.pl` + `flamegraph.pl` (Brendan Gregg's FlameGraph
    repo); None -> skip the flamegraph step with a NOTE.
    """
    logger.info("load_collection_yaml", path=str(scenario_dir / "collection.yaml"))
    if cfg is None:
        cfg = CollectionConfig.from_yaml(scenario_dir / "collection.yaml")
    if metrics is None:
        from harness.metrics_collector import MetricsCollector

        logger.info("devkit_cmd_resolved", devkit_cmd=devkit_cmd)
        metrics = MetricsCollector(devkit_cmd=devkit_cmd)

    out_dir = scenario_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    project_dir = pathlib.Path(binary).resolve().parent
    # ensure config.json exists (reference binary reads argv[1]=config path).
    cfg_path = project_dir / "config.json"
    if not cfg_path.exists():
        cfg_path.write_text("{}")

    # LLC-miss gate (only memory_bound sets llc_miss_floor_pct > 0).
    if cfg.llc_miss_floor_pct > 0.0:
        logger.info(
            "llc_miss_gate_required",
            floor=cfg.llc_miss_floor_pct,
            per_worker_buffer_mb=cfg.per_worker_buffer_mb,
        )
        miss_rate = _llc_miss_rate(binary, cfg, project_dir)
        if miss_rate < cfg.llc_miss_floor_pct:
            logger.warning(
                "llc_miss_rate_below_floor",
                miss_rate=miss_rate,
                floor=cfg.llc_miss_floor_pct,
                per_worker_buffer_mb=cfg.per_worker_buffer_mb,
            )
            return 2
        logger.info("llc_miss_gate_ok", miss_rate=miss_rate, floor=cfg.llc_miss_floor_pct)
    else:
        logger.info("llc_miss_gate_skipped")

    launch = [
        *numactl_taskset_prefix(cfg.cpu_mask, cfg.numa_node),
        binary,
        str(cfg_path),
    ]
    logger.info("launch", cmd=" ".join(launch))
    perf_rec = subprocess.Popen(
        [
            "perf",
            "record",
            "-g",
            "-F",
            str(cfg.perf_freq),
            "-o",
            str(out_dir / "perf.data"),
            "--",
            *launch,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    logger.info("wait_for_marker", marker=_MARKER, budget=cfg.warmup_seconds + 30)
    if not _wait_for_marker(perf_rec, cfg.warmup_seconds + 30):
        logger.error("marker_timeout")
        perf_rec.kill()
        return 1
    logger.info("marker_seen_start_collection")

    td_path = out_dir / "topdown.txt"
    logger.info(
        "collect_topdown_start",
        duration=cfg.measurement_seconds,
        interval=cfg.interval_seconds,
        pid=perf_rec.pid,
    )
    coll = metrics.collect_topdown(  # type: ignore[attr-defined]
        td_path,
        duration=cfg.measurement_seconds,
        interval=cfg.interval_seconds,
        pid=perf_rec.pid,
    )
    if not coll.success or coll.topdown_path is None:
        logger.error("collect_topdown_failed", error=coll.error)
        perf_rec.kill()
        return 1
    try:
        perf_rec.wait(timeout=cfg.measurement_seconds + 30)
    except subprocess.TimeoutExpired:
        perf_rec.kill()

    logger.info("parse_topdown", path=str(out_dir / "topdown.json"))
    profile = metrics.parse_topdown_file(pathlib.Path(coll.topdown_path))  # type: ignore[attr-defined]
    (out_dir / "topdown.json").write_text(profile.model_dump_json(indent=2))

    # Flamegraph (non-fatal): perf script | stackcollapse-perf.pl | flamegraph.pl.
    # Mirrors the operator's proven pipeline; both scripts live in flamegraph_dir
    # (Brendan Gregg's FlameGraph repo). Missing scripts -> NOTE, not a silent skip.
    logger.info("flamegraph_step", flamegraph_dir=flamegraph_dir)
    if flamegraph_dir is not None:
        import contextlib

        collapse = pathlib.Path(flamegraph_dir) / "stackcollapse-perf.pl"
        flame = pathlib.Path(flamegraph_dir) / "flamegraph.pl"
        if collapse.is_file() and flame.is_file():
            with contextlib.suppress(Exception):
                subprocess.run(
                    f"perf script -i {out_dir / 'perf.data'} | "
                    f"{collapse} | {flame} "
                    f"--title='mirage reference profile' "
                    f"> {out_dir / 'flamegraph.svg'}",
                    shell=True,
                    check=False,
                    timeout=120,
                )
            if (out_dir / "flamegraph.svg").is_file():
                logger.info("wrote_flamegraph", path=str(out_dir / "flamegraph.svg"))
            else:
                logger.warning("flamegraph_no_svg", reason="scripts ran but no svg produced")
        else:
            logger.warning(
                "flamegraph_scripts_missing",
                collapse=str(collapse),
                flame=str(flame),
            )

    td = profile.topdown
    if td is not None:
        logger.info(
            "captured_l1",
            frontend=td.frontend_bound,
            backend=td.backend_bound,
            bad_spec=td.bad_speculation,
            retiring=td.retiring,
        )
    return 0
