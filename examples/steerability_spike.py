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

Run on the ARM target (needs cmake/make, perf, and the devkit):

    python examples/steerability_spike.py \
        --devkit-cmd /opt/devkit/bin/devkit \
        --out-dir ./spike_out

Collection notes (open questions in RFC 0003):
  - collect_topdown is system-wide (devkit samples the whole core/box). On a
    quiet target during the workload window this is acceptable; it is the
    attribution caveat, not a correctness bug.
  - collect_flamegraph(pid=...) IS workload-attached and is used only for
    structural sanity (call-path presence), not for the sensitivity verdict.
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
from typing import TYPE_CHECKING, Any

# Make src importable without `pip install -e .` (spike is run in-place on ARM).
_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

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


def run_one_point(
    point_id: str,
    instr: dict[str, Any],
    out_root: pathlib.Path,
    build_runner: BuildRunner,
    collector: MetricsCollector,
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

    # Launch the workload in the background; it loops warmup + measurement.
    try:
        proc = subprocess.Popen(
            [binary, config_path],
            cwd=project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        return {"point_id": point_id, "error": f"binary_launch_failed: {exc}"}

    td_path = project_dir / "topdown.json"
    fg_path = project_dir / "flamegraph_folded.txt"
    td_result: dict[str, CollectionResult | None] = {"result": None}
    fg_result: dict[str, CollectionResult | None] = {"result": None}

    def collect_topdown() -> None:
        td_result["result"] = collector.collect_topdown(td_path, measurement_seconds)

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

    td = td_result["result"]
    if td is None or not td.success or td.topdown_path is None:
        err = getattr(td, "error", "no_topdown_result") if td else "no_topdown_result"
        return {"point_id": point_id, "error": f"collect_topdown_failed: {err}"}

    profile = TopdownParser().parse_json(pathlib.Path(td.topdown_path))
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
    p.add_argument("--devkit-cmd", default=None, help="devkit binary path; None disables topdown")
    p.add_argument("--perf-cmd", default="perf", help="perf binary path")
    p.add_argument("--measurement-seconds", type=int, default=20)
    p.add_argument("--warmup-seconds", type=int, default=5)
    p.add_argument("--only-knob", default="", help="comma-separated knob names to subset the sweep")
    args = p.parse_args()
    if args.devkit_cmd is None:
        print(
            "[spike] WARNING: --devkit-cmd not set; topdown collection will fail every point.",
            file=sys.stderr,
        )
    return run_spike(args)


if __name__ == "__main__":
    raise SystemExit(main())
