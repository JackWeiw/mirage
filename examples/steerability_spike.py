"""Steerability spike — prove the workload plant is controllable before wiring the auto-iteration loop.

This is a disposable measurement harness, not production code. It answers one
question: do the synthesis knobs move Topdown L1 (frontend/backend/retiring/bad-
spec) in a readable, monotonic direction? It is the empirical gate for RFC 0003
— do not wire the Phase 2 auto-iteration loop until this spike's per-knob
verdict is "controllable" for the key microarch knobs.

What it does, per sweep point:
  1. deep-copy a hand-authored BASE_INSTRUCTION (local-only, no LLM — the agent
     is NOT what we are measuring; the plant is),
  2. mutate one knob (OAT: low/mid/high),
  3. regenerate via WorkloadGenerator, then overwrite config_loader.h with a
     nlohmann-free baked version (the production template pulls nlohmann/json,
     which is not on a bare ARM toolchain), build via BuildRunner,
  4. launch the binary pinned to a CPU (taskset), warm up, then collect Topdown
     during the measurement window via `devkit tuner top-down`,
  5. parse the L1 percentages out of devkit's TEXT report (not JSON),
  6. record a row.

Memory bandwidth is NOT collected here — the devkit top-down text report has
no bandwidth number; backend_bound (and its L3-Bound sub-metric) is the memory
proxy. Bandwidth needs a separate collector and is out of spike scope.

It then writes sensitivity.json (raw rows) + sensitivity.md (per-knob table with
direction and a controllable/weak/dead verdict).

Run on the ARM target (needs cmake/make, devkit, taskset; devkit typically
requires root):

    python examples/steerability_spike.py \
        --devkit-cmd /opt/devkit/bin/devkit \
        --cpu-range 4 \
        --out-dir ./spike_out

    # smoke-test one knob first to validate the full chain cheaply:
    --only-knob working_set_mb

Collection method (resolves RFC 0003's open question on topdown attribution):
  - The workload is launched under `taskset -c <cpu_range>` so it stays on the
    measured core (no scheduler migration diluting per-core counters). It runs
    a few seconds longer than the devkit collection so it is still alive when
    devkit finishes (avoids a `-p <pid>` race when the process exits early).
  - Topdown: `devkit tuner top-down -d <dur> -i 3 -p <pid>`, text report
    captured from stdout. `-p` attributes to the workload process. devkit
    forbids `-p` and `--cpu` together, so we do NOT pass `--cpu` — the
    taskset pin already places the process on the measured core, and process
    attribution is more precise than system-wide-on-core. (NOTE:
    MetricsCollector in src/ uses an older wrong devkit CLI and is NOT used
    here — fixing it is a follow-up for the production loop path.)
  - devkit emits a TEXT report (a human-readable table), not JSON — parsed by
    _parse_topdown_text, not TopdownParser (whose JSON-schema assumption was
    wrong). Flamegraph/perf is not collected — the sensitivity verdict is
    based on Topdown L1 alone, which removes a perf dependency.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import types
from typing import Any

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
# is enough (no __init__ body to exec). codegen transitively imports
# profile.profile_schema (call_tree, module_graph_builder), so this matters.
_profile_pkg = types.ModuleType("profile")
_profile_pkg.__path__ = [str(_SRC / "profile")]
sys.modules["profile"] = _profile_pkg

from codegen.generator import WorkloadGenerator  # noqa: E402
from harness.build_runner import BuildRunner  # noqa: E402

# Workload runs this many seconds longer than the devkit collection so the
# process is still alive when devkit finishes (avoids the -p <pid> exit race).
_WORKLOAD_MEASUREMENT_PAD_S = 3


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


def _write_baked_config_loader(project_dir: pathlib.Path, config: dict[str, Any]) -> None:
    """Overwrite the generated config_loader.h with a nlohmann-free version.

    The production config_loader.h.j2 hardcodes `#include <nlohmann/json.hpp>`,
    which fails on a bare ARM toolchain without that header installed (the
    instruction carries dependencies:[], so CMake does not pull nlohmann in
    either). The spike already controls the runtime values via the instruction,
    so bake them in at compile time and skip JSON parsing entirely. (main.cpp
    only reads thread_count/qps/warmup_seconds/measurement_seconds —
    compute_ratio/memory_ratio are baked but unused, and that inertness is
    itself a spike finding recorded in sensitivity.json.)
    """
    content = (
        "#pragma once\n"
        "#include <string>\n\n"
        "struct RunConfig {\n"
        "    int thread_count;\n"
        "    int qps;\n"
        "    int warmup_seconds;\n"
        "    int measurement_seconds;\n"
        "    double compute_ratio;\n"
        "    double memory_ratio;\n"
        "};\n\n"
        "inline RunConfig load_config(const std::string&) {\n"
        "    return RunConfig{" + f"{config.get('thread_count', 4)}, {config.get('qps', 100)}, "
        f"{config.get('warmup_seconds', 30)}, "
        f"{config.get('measurement_seconds', 60)}, "
        f"{config.get('compute_ratio', 0.5)}, {config.get('memory_ratio', 0.5)}" + "};\n"
        "}\n"
    )
    (project_dir / "config_loader.h").write_text(content)


# devkit `tuner top-down` emits a human-readable table, e.g.:
#   Backend Bound                              72.01    --
#   Frontend Bound                            17.59    --
#   Bad Speculation                            3.01    --
#   Retiring                                    7.38    --
# Pull the four L1 category percentages (case-insensitive label, first float).
_TOPDOWN_L1_RE = re.compile(
    r"^\s*(backend bound|frontend bound|bad speculation|retiring)\s+([\d.]+)",
    re.IGNORECASE | re.MULTILINE,
)


def _parse_topdown_text(path: pathlib.Path) -> dict[str, float | None]:
    """Parse the devkit top-down TEXT report into L1 percentages.

    Returns None for any category not found (so a format change surfaces as a
    missing value rather than a crash). All four None -> caller treats it as a
    parse failure and surfaces the raw text.
    """
    text = path.read_text(errors="replace")
    found = {label.lower(): float(val) for label, val in _TOPDOWN_L1_RE.findall(text)}
    return {
        "frontend_bound": found.get("frontend bound"),
        "backend_bound": found.get("backend bound"),
        "bad_speculation": found.get("bad speculation"),
        "retiring": found.get("retiring"),
    }


def _collect_topdown_devkit(
    devkit_cmd: str,
    duration: int,
    interval: int,
    pid: int,
    out_path: pathlib.Path,
) -> tuple[bool, str | None]:
    """Run `devkit tuner top-down -d <dur> -i <int> -p <pid>`.

    Captures the devkit's TEXT report stdout to out_path. Returns (ok, error).
    `-p` attributes topdown to the workload process (not system-wide).

    devkit rejects `-p/--pid` together with `-c/--cpu` ("Options ... cannot be
    used together"), so the two scopes are mutually exclusive. We use `-p`
    (process attribution) and rely on the workload already being taskset-pinned
    to the target core for core placement — the process runs on that core, so its
    attributed topdown is the on-core breakdown we want. Process attribution is
    also more precise than system-wide-on-core, and it answers RFC 0003's open
    question (yes, devkit topdown is workload-attributable, not just system-wide).
    """
    cmd = [
        devkit_cmd,
        "tuner",
        "top-down",
        "-d",
        str(duration),
        "-i",
        str(interval),
        "-p",
        str(pid),
    ]
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
    devkit_cmd: str,
    devkit_interval: int,
    cpu_range: str | None,
    measurement_seconds: int,
    warmup_seconds: int,
) -> dict[str, Any]:
    """Regenerate, bake config, build, run+collect, parse — return one row."""
    project_dir = out_root / point_id
    project_dir.mkdir(parents=True, exist_ok=True)

    WorkloadGenerator().generate(instr, project_dir)
    # Replace the nlohmann-dependent generated header with a baked one so the
    # project builds on a bare ARM toolchain. See _write_baked_config_loader.
    _write_baked_config_loader(project_dir, instr["config"])

    build = build_runner.build(project_dir)
    if not build.success:
        return {"point_id": point_id, "error": f"build_failed: {build.stderr[:300]}"}

    # CMake names the executable after project_name (add_executable(<name>)),
    # so locate it directly and verify it is executable. BuildRunner's binary
    # detection picks the first suffixless file in the build tree, which on
    # this box is the Makefile (no extension -> not excluded) -- taskset then
    # tries to exec the Makefile (rc=127). The executable-bit check rules the
    # Makefile out. (BuildRunner's rglob pick is a production bug; flagged as a
    # follow-up so the auto-loop doesn't hit it too.)
    binary = project_dir / "build" / instr["project_name"]
    if not (binary.is_file() and os.access(binary, os.X_OK)):
        cand = pathlib.Path(build.binary_path) if build.binary_path else None
        if cand and cand.is_file() and os.access(cand, os.X_OK):
            binary = cand
        else:
            return {
                "point_id": point_id,
                "error": (
                    f"binary_not_found: expected {binary} "
                    f"(build.binary_path={build.binary_path}); "
                    f"build.stderr[:200]={build.stderr[:200]}"
                ),
            }
    binary = str(binary)
    config_path = str(project_dir / "config.json")

    # Launch the workload pinned to a CPU range (taskset) so its topdown
    # counters (attributed via -p <pid>) are measured on exactly the core it
    # runs on, with no scheduler migration diluting them. taskset execs the
    # binary in place, so proc.pid is the workload's own PID.
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

    td_path = project_dir / "topdown.txt"

    # Warm up, then collect topdown synchronously during the measurement window.
    time.sleep(warmup_seconds)

    # If the workload already exited during warmup (e.g. a stage segfaulted),
    # surface its stdout/stderr + return code — otherwise this shows up later
    # as a misleading devkit "no such process" that hides the real crash.
    if proc.poll() is not None:
        try:
            out, err_out = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err_out = "<communicate-timeout>", "<communicate-timeout>"
        return {
            "point_id": point_id,
            "error": (
                f"workload_exited_during_warmup rc={proc.returncode}; "
                f"stdout[:500]={(out or '')[:500]}; "
                f"stderr[:500]={(err_out or '')[:500]}"
            ),
        }

    ok, err = _collect_topdown_devkit(
        devkit_cmd, measurement_seconds, devkit_interval, proc.pid, td_path
    )

    # The workload runs measurement_seconds + pad; devkit collected for
    # measurement_seconds, so the process should be exiting/exitable now.
    try:
        proc.wait(timeout=_WORKLOAD_MEASUREMENT_PAD_S + 30)
    except subprocess.TimeoutExpired:
        proc.kill()

    if not ok:
        return {"point_id": point_id, "error": f"collect_topdown_failed: {err}"}

    td1 = _parse_topdown_text(td_path)
    if all(v is None for v in td1.values()):
        raw = td_path.read_text(errors="replace")[:300]
        return {
            "point_id": point_id,
            "error": f"topdown_parse_failed: no L1 lines; raw[:300]={raw}",
        }
    return {
        "point_id": point_id,
        "topdown_l1": td1,
        # devkit top-down text report has no bandwidth; backend_bound (and its
        # L3-Bound sub-metric) is the memory proxy. Out of spike scope.
        "memory_bandwidth_gbps": None,
    }


def verdict_for(knob: str, target_metric: str, expected: str, rows: list[dict[str, Any]]) -> str:
    """Classify a knob's effect: controllable / weak / dead.

    controllable: target_metric is monotonic across low->high and the direction
    matches `expected`. weak: moves but non-monotonic or wrong direction. dead:
    no movement (all equal).
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

    only = set(args.only_knob.split(",")) if args.only_knob else None
    rows: list[dict[str, Any]] = []
    for sweep in SWEEPS:
        if only and sweep["knob"] not in only:
            continue
        for value in sweep["values"]:
            point_id = f"{sweep['knob']}_{value}"
            # Bake the workload's measurement window a few seconds longer than
            # the devkit collection so the process outlives devkit (see header).
            instr = copy.deepcopy(
                base_instruction(
                    args.measurement_seconds + _WORKLOAD_MEASUREMENT_PAD_S, args.warmup_seconds
                )
            )
            sweep["mutator"](instr, value)
            print(f"[spike] {point_id}: build + run + collect ...", flush=True)
            row = run_one_point(
                point_id,
                instr,
                out_root,
                build_runner,
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
                td1 = row["topdown_l1"]
                print(
                    f"[spike] {point_id}: backend={td1['backend_bound']} "
                    f"retiring={td1['retiring']}",
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
    p.add_argument(
        "--cpu-range",
        default=None,
        help="CPU range to pin the workload via taskset so its topdown counters "
        "stay on one core, e.g. '4' or '4-7' or '1-39,120-159'",
    )
    p.add_argument("--devkit-interval", type=int, default=3, help="devkit -i interval")
    p.add_argument("--measurement-seconds", type=int, default=20)
    p.add_argument("--warmup-seconds", type=int, default=5)
    p.add_argument("--only-knob", default="", help="comma-separated knob names to subset the sweep")
    args = p.parse_args()
    if args.cpu_range is None:
        print(
            "[spike] WARNING: --cpu-range not set; workload not pinned via taskset "
            "— it may migrate across cores, diluting the per-process topdown "
            "attribution. Set it to an isolated core for precise results.",
            file=sys.stderr,
        )
    return run_spike(args)


if __name__ == "__main__":
    raise SystemExit(main())
