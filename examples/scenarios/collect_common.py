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
    print(f"[1/6] load collection.yaml from {scenario_dir / 'collection.yaml'}")
    if cfg is None:
        cfg = CollectionConfig.from_yaml(scenario_dir / "collection.yaml")
    if metrics is None:
        from harness.metrics_collector import MetricsCollector

        print(f"[1/6] devkit_cmd={devkit_cmd!r}")
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
        print(
            f"[2/6] LLC-miss gate: require >{cfg.llc_miss_floor_pct}% "
            f"(per_worker_buffer_mb={cfg.per_worker_buffer_mb})"
        )
        miss_rate = _llc_miss_rate(binary, cfg, project_dir)
        if miss_rate < cfg.llc_miss_floor_pct:
            print(
                f"WARNING: LLC-miss rate {miss_rate:.1f}% < {cfg.llc_miss_floor_pct}% "
                f"— per_worker_buffer_mb={cfg.per_worker_buffer_mb} is too small for "
                f"this box's LLC. Increase it in collection.yaml and re-capture."
            )
            return 2
        print(f"[2/6] LLC-miss rate {miss_rate:.1f}% >= {cfg.llc_miss_floor_pct}% OK")
    else:
        print("[2/6] LLC-miss gate: skipped (scenario does not set a floor)")

    launch = [
        *numactl_taskset_prefix(cfg.cpu_mask, cfg.numa_node),
        binary,
        str(cfg_path),
    ]
    print(f"[3/6] launch: {' '.join(launch)}")
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

    print(f"[4/6] wait for steady-state marker {_MARKER!r} (warmup+30s budget)")
    if not _wait_for_marker(perf_rec, cfg.warmup_seconds + 30):
        print("ERROR: binary did not print the measurement marker in time.")
        perf_rec.kill()
        return 1
    print("[4/6] marker seen -> start topdown collection")

    td_path = out_dir / "topdown.txt"
    print(
        f"[5/6] collect_topdown duration={cfg.measurement_seconds}s "
        f"interval={cfg.interval_seconds}s pid={perf_rec.pid}"
    )
    coll = metrics.collect_topdown(  # type: ignore[attr-defined]
        td_path,
        duration=cfg.measurement_seconds,
        interval=cfg.interval_seconds,
        pid=perf_rec.pid,
    )
    if not coll.success or coll.topdown_path is None:
        print(f"ERROR: collect_topdown failed: {coll.error}")
        perf_rec.kill()
        return 1
    try:
        perf_rec.wait(timeout=cfg.measurement_seconds + 30)
    except subprocess.TimeoutExpired:
        perf_rec.kill()

    print(f"[5/6] parse topdown -> {out_dir / 'topdown.json'}")
    profile = metrics.parse_topdown_file(pathlib.Path(coll.topdown_path))  # type: ignore[attr-defined]
    (out_dir / "topdown.json").write_text(profile.model_dump_json(indent=2))

    # Flamegraph (non-fatal): perf script | stackcollapse-perf.pl | flamegraph.pl.
    # Mirrors the operator's proven pipeline; both scripts live in flamegraph_dir
    # (Brendan Gregg's FlameGraph repo). Missing scripts -> NOTE, not a silent skip.
    print(f"[6/6] flamegraph (dir={flamegraph_dir!r})")
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
                print(f"[6/6] wrote {out_dir / 'flamegraph.svg'}")
            else:
                print("NOTE: flamegraph scripts ran but no .svg produced (perf.data empty?)")
        else:
            print(
                f"NOTE: skipping flamegraph — {collapse} / {flame} not found. "
                f"Clone https://github.com/brendangregg/FlameGraph and pass --flamegraph-dir."
            )

    td = profile.topdown
    if td is not None:
        print(
            f"Captured L1: frontend={td.frontend_bound:.1f} backend={td.backend_bound:.1f} "
            f"bad_spec={td.bad_speculation:.1f} retiring={td.retiring:.1f}"
        )
    return 0
