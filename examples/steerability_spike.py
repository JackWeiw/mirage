"""Steerability spike — prove the workload plant is controllable before wiring the auto-iteration loop.

This is a disposable measurement harness, not production code. It answers one
question: do the synthesis knobs move Topdown L1 and memory bandwidth in a
readable, monotonic direction? It is the empirical gate for RFC 0003 — do not
wire the Phase 2 auto-iteration loop until this spike's per-knob verdict is
"controllable" for the key microarch knobs.

What it does, per sweep point:
  1. deep-copy a hand-authored BASE_INSTRUCTION (local-only, no LLM — the agent
     is NOT what we are measuring; the plant is),
  2. mutate one knob (OAT: low/mid/high),
  3. regenerate via WorkloadGenerator, build via BuildRunner,
  4. run the binary in the background and collect Topdown + flamegraph during
     the measurement window via MetricsCollector,
  5. parse the devkit JSON for absolute Topdown L1 + memory bandwidth,
  6. record a row.

It then writes sensitivity.json (raw rows) + sensitivity.md (per-knob table
with direction and a controllable/weak/dead verdict).

Run on the ARM target (needs cmake/make, perf, devkit, and taskset; perf/devkit
typically require root or relaxed perf_event_paranoid):

    python examples/steerability_spike.py \
        --devkit-cmd /opt/devkit/bin/devkit \
        --cpu-range 4 \
        --out-dir ./spike_out

    # smoke-test one knob first to validate the full chain cheaply:
    --only-knob working_set_mb

Collection method (resolves RFC 0003's open question on topdown attribution):
  - The workload is launched under `taskset -c <cpu_range>` so it stays on the
    measured cores (no scheduler migration diluting per-core counters).
  - Topdown: `devkit tuner top-down -d <dur> -i 3 -c <cpu> -p <pid>`, JSON
    captured from stdout. `-p` attributes to the workload process; `-c` scopes
    counters to the pinned cores. (NOTE: MetricsCollector.collect_topdown in
    src/ uses an older wrong devkit CLI and is NOT used here — fixing it is a
    follow-up for the production loop path.)
  - Flamegraph: perf record -g -p <pid> (already process-attached via
    MetricsCollector.collect_flamegraph); used only for structural sanity, not
    the sensitivity verdict.
  - The devkit JSON is assumed to match TopdownParser's schema (topdown_l1 /
    memory keys). If it does not, the parse error surfaces the raw output so the
    mapping can be fixed instead of failing silently.
"""

from __future__ import annotations

import argparse
import copy
import json
import pathlib
import subprocess
import sys
import threading
import time
import types
from typing import TYPE_CHECKING, Any

# Make src importable without `pip install -e .` (spike is run in-place on ARM).
_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))

# The project ships a top-level package named `profile`, which collides with
# CPython's stdlib `profile` (the profiler — a single-file module). Under
# pytest/mypy (pythonpath/mypy_path = src) the project's package wins; run
# bare, the stdlib one can be picked instead ("'profile' is not a package"),
# depending on how mirage is editable-installed. Pre-seed sys.modules with our
# package pointing at src/profile so `from profile.* import ...` always resolves
# there. src/profile/__init__.py is empty, so a bare module with __path__ set
# is enough (no __init__ body to exec).
_profile_pkg = types.ModuleType("profile")
_profile_pkg.__path__ = [str(_SRC / "profile")]
sys.modules["profile"] = _profile_pkg

from codegen.generator import WorkloadGenerator  # noqa: E402
from harness.build_runner import BuildRunner  # noqa: E402
from harness.metrics_collector import MetricsCollector  # noqa: E402
from ingestion.topdown_parser import TopdownParser  # noqa: E402

if TYPE_CHECKING:
    from models.results import CollectionResult


def base_instruction(measurement_seconds: int, warmup_seconds: int) -> dict[str, Any]:
    """Hand-authored minimal instruction: one memory + one compute stage.

    No direct_call/open-source leaf and no external libs, so the project builds
    on a bare ARM toolchain without third-party dependencies. Coverage is
    validated separately (it is structural, not knob-responsive) and is out of
    scope for this sweep.
    """
    return {
        "project_name": "steerability_spike",
        "compile_flags": "-O2 -march=armv8.2-a -fno-inline-small-functions",
        "dependencies": [],
        "dep_headers": [],
        "stages": [
            {
                "implementation_strategy": "memory_synthesis",
                "stage_name": "mem_stage",
                "strategies": [
                    {
                        "strategy": "memory_synthesis",
                        "synthesis_config": {
                            "iterations": 100,
                            "working_set_mb": 64,
                            "access_pattern": "random",
                        },
                    }
                ],
            },
            {
                "implementation_strategy": "compute_synthesis",
                "stage_name": "comp_stage",
                "strategies": [
                    {
                        "strategy": "compute_synthesis",
                        "synthesis_config": {"archetype": "compute", "iterations": 100},
                    }
                ],
            },
        ],
        "config": {
            "thread_count": 4,
            "qps": 100,
            "warmup_seconds": warmup_seconds,
            "measurement_seconds": measurement_seconds,
            "compute_ratio": 0.5,
            "memory_ratio": 0.5,
            "ramp_up_seconds": 5,
        },
    }


# Each knob: (name, target_metric, expected_direction, ordered_values, mutator).
# ordered_values are low -> high in the *expected* direction of target_metric.
# mutator(instr, value) applies the knob in place.
def _set_mem_cfg(instr: dict[str, Any], key: str, value: Any) -> None:
    instr["stages"][0]["strategies"][0]["synthesis_config"][key] = value


def _set_comp_cfg(instr: dict[str, Any], key: str, value: Any) -> None:
    instr["stages"][1]["strategies"][0]["synthesis_config"][key] = value


def _set_config(instr: dict[str, Any], key: str, value: Any) -> None:
    instr["config"][key] = value


SWEEPS: list[dict[str, Any]] = [
    {
        "knob": "working_set_mb",
        "target_metric": "backend_bound",
        "expected": "up",
        "values": [16, 64, 256],
        "mutator": lambda instr, v: _set_mem_cfg(instr, "working_set_mb", v),
    },
    {
        "knob": "access_pattern",
        "target_metric": "backend_bound",
        "expected": "up",  # sequential(low) -> mixed -> random(high) backend stalls
        "values": ["sequential", "mixed", "random"],
        "mutator": lambda instr, v: _set_mem_cfg(instr, "access_pattern", v),
    },
    {
        "knob": "iterations_mem",
        "target_metric": "backend_bound",
        "expected": "up",
        "values": [10, 100, 1000],
        "mutator": lambda instr, v: _set_mem_cfg(instr, "iterations", v),
    },
    {
        "knob": "archetype",
        "target_metric": "retiring",
        "expected": "up",  # compute -> hash -> matmul retires more useful work
        "values": ["compute", "hash", "matmul"],
        "mutator": lambda instr, v: _set_comp_cfg(instr, "archetype", v),
    },
    {
        "knob": "compute_ratio",
        "target_metric": "retiring",
        "expected": "up",
        "values": [0.2, 0.5, 0.8],
        "mutator": lambda instr, v: _set_config(instr, "compute_ratio", v),
    },
    {
        "knob": "memory_ratio",
        "target_metric": "backend_bound",
        "expected": "up",
        "values": [0.2, 0.5, 0.8],
        "mutator": lambda instr, v: _set_config(instr, "memory_ratio", v),
    },
]


def _collect_topdown_devkit(
    devkit_cmd: str,
    duration: int,
    interval: int,
    cpu_range: str | None,
    pid: int,
    out_path: pathlib.Path,
) -> tuple[bool, str | None]:
    """Run `devkit tuner top-down -d <dur> -i <int> [-c <cpu>] -p <pid>`.

    Captures the devkit's JSON stdout to out_path. Returns (ok, error).
    `-p` attributes topdown to the workload process (not system-wide); `-c`
    scopes the per-core counters to the same cores the workload is pinned to.
    """
    cmd = [devkit_cmd, "tuner", "top-down", "-d", str(duration), "-i", str(interval)]
    if cpu_range:
        cmd += ["-c", cpu_range]
    cmd += ["-p", str(pid)]
    try:
        with open(out_path, "w") as stdout_f:
            result = subprocess.run(
                cmd,
                stdout=stdout_f,
                stderr=subprocess.PIPE,
                text=True,
                timeout=duration + 30,
                check=False,
            )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, f"devkit_launch: {exc}"
    if result.returncode != 0:
        return False, f"devkit rc={result.returncode}: {result.stderr[:300]}"
    return True, None


def run_one_point(
    point_id: str,
    instr: dict[str, Any],
    out_root: pathlib.Path,
    build_runner: BuildRunner,
    collector: MetricsCollector,
    devkit_cmd: str,
    devkit_interval: int,
    cpu_range: str | None,
    measurement_seconds: int,
    warmup_seconds: int,
) -> dict[str, Any]:
    """Regenerate, build, run+collect, parse — return one sensitivity row."""
    project_dir = out_root / point_id
    project_dir.mkdir(parents=True, exist_ok=True)

    WorkloadGenerator().generate(instr, project_dir)

    build = build_runner.build(project_dir)
    if not build.success or not build.binary_path:
        return {"point_id": point_id, "error": f"build_failed: {build.stderr[:300]}"}

    binary = build.binary_path
    config_path = str(project_dir / "config.json")

    # Launch the workload pinned to a CPU range (taskset) so the per-core
    # topdown counters in `-c` measure exactly the cores it runs on. taskset
    # execs the binary in place, so proc.pid is the workload's own PID.
    launch_cmd: list[str] = [binary, config_path]
    if cpu_range:
        launch_cmd = ["taskset", "-c", cpu_range, binary, config_path]
    try:
        proc = subprocess.Popen(
            launch_cmd,
            cwd=project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        return {"point_id": point_id, "error": f"binary_launch_failed: {exc}"}

    td_path = project_dir / "topdown.json"
    fg_path = project_dir / "flamegraph_folded.txt"
    td_out: dict[str, Any] = {"ok": False, "error": None}
    fg_result: dict[str, CollectionResult | None] = {"result": None}

    def collect_topdown() -> None:
        ok, err = _collect_topdown_devkit(
            devkit_cmd, measurement_seconds, devkit_interval, cpu_range, proc.pid, td_path
        )
        td_out["ok"] = ok
        td_out["error"] = err

    def collect_flamegraph() -> None:
        fg_result["result"] = collector.collect_flamegraph(
            fg_path, measurement_seconds, pid=proc.pid
        )

    # Warm up, then collect both concurrently during the measurement window.
    time.sleep(warmup_seconds)
    t_td = threading.Thread(target=collect_topdown)
    t_fg = threading.Thread(target=collect_flamegraph)
    t_td.start()
    t_fg.start()
    t_td.join()
    t_fg.join()

    try:
        proc.wait(timeout=measurement_seconds + 30)
    except subprocess.TimeoutExpired:
        proc.kill()

    if not td_out["ok"]:
        return {"point_id": point_id, "error": f"collect_topdown_failed: {td_out['error']}"}

    # Parse the devkit JSON. If the schema doesn't match TopdownParser's
    # expected keys (topdown_l1 / memory), surface the raw output so the
    # mapping can be fixed instead of failing silently.
    try:
        profile = TopdownParser().parse_json(td_path)
    except (ValueError, OSError) as exc:
        raw = td_path.read_text(errors="replace")[:300]
        return {
            "point_id": point_id,
            "error": f"topdown_parse_failed: {exc}; raw[:300]={raw}",
        }

    td1 = profile.topdown
    mem = profile.memory
    return {
        "point_id": point_id,
        "topdown_l1": {
            "frontend_bound": td1.frontend_bound if td1 else None,
            "backend_bound": td1.backend_bound if td1 else None,
            "bad_speculation": td1.bad_speculation if td1 else None,
            "retiring": td1.retiring if td1 else None,
        },
        "memory_bandwidth_gbps": mem.bandwidth_gbps if mem else None,
        "flamegraph_path": str(fg_path)
        if fg_result["result"] and fg_result["result"].success
        else None,
    }


def verdict_for(knob: str, target_metric: str, expected: str, rows: list[dict[str, Any]]) -> str:
    """Classify a knob's effect: controllable / weak / dead.

    controllable: target_metric is strictly monotonic across low->high and the
    direction matches `expected`. weak: moves but non-monotonic or wrong
    direction. dead: no movement (all equal).
    """
    vals = [r["topdown_l1"][target_metric] for r in rows if r.get("topdown_l1")]
    if len(vals) != len(rows) or not vals:
        return "dead"
    if all(v == vals[0] for v in vals):
        return "dead"
    increasing = all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1))
    decreasing = all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1))
    if not (increasing or decreasing):
        return "weak"
    direction = "up" if increasing else "down"
    return "controllable" if direction == expected else "weak"


def run_spike(args: argparse.Namespace) -> int:
    out_root = pathlib.Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    build_runner = BuildRunner()
    collector = MetricsCollector(devkit_cmd=args.devkit_cmd, perf_cmd=args.perf_cmd)

    only = set(args.only_knob.split(",")) if args.only_knob else None
    rows: list[dict[str, Any]] = []
    for sweep in SWEEPS:
        if only and sweep["knob"] not in only:
            continue
        for value in sweep["values"]:
            point_id = f"{sweep['knob']}_{value}"
            instr = copy.deepcopy(base_instruction(args.measurement_seconds, args.warmup_seconds))
            sweep["mutator"](instr, value)
            print(f"[spike] {point_id}: build + run + collect ...", flush=True)
            row = run_one_point(
                point_id,
                instr,
                out_root,
                build_runner,
                collector,
                args.devkit_cmd,
                args.devkit_interval,
                args.cpu_range,
                args.measurement_seconds,
                args.warmup_seconds,
            )
            row["knob"] = sweep["knob"]
            row["value"] = value
            row["target_metric"] = sweep["target_metric"]
            row["expected"] = sweep["expected"]
            if "error" in row:
                print(f"[spike] {point_id}: ERROR {row['error']}", flush=True)
            else:
                bb = row["topdown_l1"]["backend_bound"]
                print(
                    f"[spike] {point_id}: backend={bb} bw={row['memory_bandwidth_gbps']}",
                    flush=True,
                )
            rows.append(row)

    # Per-knob verdicts.
    verdicts: list[dict[str, Any]] = []
    for sweep in SWEEPS:
        if only and sweep["knob"] not in only:
            continue
        knob_rows = [r for r in rows if r["knob"] == sweep["knob"]]
        good = [r for r in knob_rows if "error" not in r]
        if not good:
            verdicts.append(
                {"knob": sweep["knob"], "verdict": "dead", "reason": "all points errored"}
            )
            continue
        v = verdict_for(sweep["knob"], sweep["target_metric"], sweep["expected"], good)
        verdicts.append(
            {
                "knob": sweep["knob"],
                "target_metric": sweep["target_metric"],
                "expected": sweep["expected"],
                "verdict": v,
                "values": [r["value"] for r in good],
                "metric_values": [r["topdown_l1"][sweep["target_metric"]] for r in good],
            }
        )

    (out_root / "sensitivity.json").write_text(
        json.dumps({"rows": rows, "verdicts": verdicts}, indent=2)
    )

    # Human-readable table.
    lines = ["# Steerability spike — results", ""]
    lines.append("| knob | target_metric | expected | values | metric_values | verdict |")
    lines.append("|---|---|---|---|---|---|")
    for vd in verdicts:
        lines.append(
            f"| {vd['knob']} | {vd.get('target_metric', '-')} | {vd.get('expected', '-')} | "
            f"{vd.get('values')} | {vd.get('metric_values')} | {vd['verdict']} |"
        )
    lines.append("")
    lines.append(
        "Gate (RFC 0003 §4): pass iff `working_set_mb`, `access_pattern`, "
        "`archetype`, `compute_ratio`, `memory_ratio` are each `controllable`."
    )
    (out_root / "sensitivity.md").write_text("\n".join(lines) + "\n")
    print(f"[spike] wrote {out_root / 'sensitivity.json'} and {out_root / 'sensitivity.md'}")

    key = {"working_set_mb", "access_pattern", "archetype", "compute_ratio", "memory_ratio"}
    controllable = {v["knob"]: v["verdict"] == "controllable" for v in verdicts if v["knob"] in key}
    passed = all(controllable.values()) if controllable else False
    print(f"[spike] gate {'PASSED' if passed else 'FAILED'}: {controllable}")
    return 0 if passed else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Steerability spike (RFC 0003 gate).")
    p.add_argument("--out-dir", default="./spike_out", help="output directory")
    p.add_argument(
        "--devkit-cmd", required=True, help="devkit binary path (e.g. /opt/devkit/bin/devkit)"
    )
    p.add_argument("--perf-cmd", default="perf", help="perf binary path")
    p.add_argument(
        "--cpu-range",
        default=None,
        help="CPU range to pin the workload (taskset) and scope devkit -c, e.g. '4' or '4-7'",
    )
    p.add_argument("--devkit-interval", type=int, default=3, help="devkit -i interval")
    p.add_argument("--measurement-seconds", type=int, default=20)
    p.add_argument("--warmup-seconds", type=int, default=5)
    p.add_argument("--only-knob", default="", help="comma-separated knob names to subset the sweep")
    args = p.parse_args()
    if args.cpu_range is None:
        print(
            "[spike] WARNING: --cpu-range not set; workload not pinned and devkit "
            "runs without -c — topdown will be process-attributed but not core-scoped "
            "(less precise).",
            file=sys.stderr,
        )
    return run_spike(args)


if __name__ == "__main__":
    raise SystemExit(main())
