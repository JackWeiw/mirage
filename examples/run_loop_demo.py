#!/usr/bin/env python3
"""Steering-validation driver: fit mirage's synthetic workload to a captured
reference Topdown via the LLM structural tier, then judge the four success criteria.

  PYTHONPATH=src python3 examples/run_loop_demo.py --scenario memory_bound \
      [--max-iter 10] [--threshold 10] [--out-dir run_out] [--no-agent] \
      [--config path/to/fw.yaml]

The LLM (structural) tier is configured via the operator's gateway env vars,
honored by FrameworkConfig.from_env (PR #64):
  MIRAGE_AGENT_API_KEY   -> gateway key   (sets the agent online)
  MIRAGE_AGENT_BASE_URL  -> the gateway   (never a vendor official host)
  MIRAGE_AGENT_PROVIDER  -> "anthropic" | "openai"
  MIRAGE_AGENT_MODEL     -> model id
Use --config to layer a YAML FrameworkConfig under those env overrides
(precedence: yaml < env). --no-agent forces offline/runtime-only regardless.
"""

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass
from typing import Any, cast

_HERE = pathlib.Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_HERE / "scenarios") not in sys.path:
    sys.path.insert(0, str(_HERE / "scenarios"))

import collect_common  # type: ignore[import-not-found]  # noqa: E402

from agent.adjustment import load_sensitivity  # noqa: E402
from agent.agent_core import AgentCore  # noqa: E402
from config.framework_config import AgentConfig, FrameworkConfig  # noqa: E402
from harness.metrics_collector import MetricsCollector  # noqa: E402
from harness.pipeline import Pipeline  # noqa: E402
from models.results import RunFailure  # noqa: E402
from observability.iteration_history import IterationHistory  # noqa: E402
from observability.logging import configure_logging_from_env, get_logger  # noqa: E402
from profile.profile_schema import Profile  # noqa: E402

logger = get_logger("run_loop_demo")

DOMINANT = {"memory_bound": "backend_bound", "compute_bound": "retiring"}


@dataclass
class CriteriaResult:
    verdict: str  # "PASS" | "FAIL" | "RUNTIME-ONLY"
    criteria: list[bool]  # [triggered, monotonic, terminal, non_dominant_cap]


def evaluate_criteria(
    history: IterationHistory, stop_reason: str, dominant: str, threshold: float
) -> CriteriaResult:
    """Judge the four success criteria (see spec §Success criteria) from history."""
    records = history.records
    # 1. Steering triggered: at least one structural (priority>=2) iteration.
    triggered = any(r.strategy_priority >= 2 for r in records)
    # 2. Monotonic: best-so-far dominant gap non-increasing, at most ONE bounce.
    best = float("inf")
    bounces = 0
    for r in records:
        gap = abs(r.topdown_diffs.get(dominant, 0.0))
        if gap > best + 1e-9:
            bounces += 1  # a rise vs the running best
        else:
            best = gap
    monotonic = bounces <= 1
    final_gap = abs(records[-1].topdown_diffs.get(dominant, 0.0)) if records else 999.0
    # 3. Terminal: converged (dominant within --threshold, as the loop's stop_reason
    #    already encodes "all L1 within threshold") OR max_iter with dominant gap
    #    <= the SPEC's fixed 10pp residual (independent of --threshold: tightening
    #    --threshold forces iteration but does not tighten the terminal residual).
    terminal = (stop_reason == "converged" and final_gap <= threshold) or (
        stop_reason == "max_iter" and final_gap <= 10.0
    )
    # 4. Non-dominant cap: every other L1 metric <= 20pp at terminal state.
    others = (
        ["frontend_bound", "bad_speculation", "retiring"]
        if dominant == "backend_bound"
        else ["frontend_bound", "backend_bound", "bad_speculation"]
        if dominant == "retiring"
        else ["frontend_bound", "backend_bound", "bad_speculation", "retiring"]
    )
    cap_ok = True
    if records:
        for m in others:
            if m == dominant:
                continue
            if abs(records[-1].topdown_diffs.get(m, 0.0)) > 20.0:
                cap_ok = False
                break
    criteria = [triggered, monotonic, terminal, cap_ok]
    verdict = "PASS" if all(criteria) else "FAIL"
    return CriteriaResult(verdict=verdict, criteria=criteria)


def _print_table(history: IterationHistory) -> None:
    print("iter | conv | prio | backend | frontend | badspec | retiring | score | failed | reason")
    for r in history.records:
        td = r.topdown_diffs
        reason = r.failure_reason if r.failed else ""
        print(
            f"{r.iteration:4d} | {r.converged!s:5s} | {r.strategy_priority:4d} | "
            f"{td.get('backend_bound', 0):7.1f} | {td.get('frontend_bound', 0):7.1f} | "
            f"{td.get('bad_speculation', 0):7.1f} | {td.get('retiring', 0):7.1f} | "
            f"{r.score} | {r.failed!s:5s} | {reason}"
        )


def main() -> int:
    configure_logging_from_env()  # before any log is emitted (env: MIRAGE_LOG_LEVEL/JSON)
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True, choices=list(DOMINANT))
    ap.add_argument("--max-iter", type=int, default=10)
    ap.add_argument("--threshold", type=float, default=10.0)
    ap.add_argument("--out-dir", default="run_out")
    ap.add_argument("--no-agent", action="store_true")
    ap.add_argument(
        "--config",
        type=pathlib.Path,
        default=None,
        help="FrameworkConfig YAML path; layered UNDER the MIRAGE_AGENT_* env "
        "overrides (precedence: yaml < env). Default: defaults().",
    )
    args = ap.parse_args()

    scen_dir = _HERE / "scenarios" / args.scenario
    profile = Profile.model_validate_json((scen_dir / "topdown.json").read_text())
    seed = json.loads((scen_dir / "seed_instruction.json").read_text())
    sens = load_sensitivity(scen_dir / "sensitivity.json")
    cfg = collect_common.CollectionConfig.from_yaml(scen_dir / "collection.yaml")

    # from_env: defaults() (or --config yaml) + MIRAGE_AGENT_* env overrides.
    # With no env and no --config this is identical to defaults() -> offline.
    fw = FrameworkConfig.from_env(args.config)
    fw.comparison.topdown_threshold_pct = args.threshold
    if args.no_agent:
        fw.agent = AgentConfig(model=fw.agent.model, api_key=None)  # -> degraded
    agent = AgentCore(fw.agent)

    # Agent gate: structural tier required unless --no-agent.
    if not args.no_agent and not agent.is_available():
        logger.error(
            "agent_unavailable",
            hint=(
                "A ~35pp gap needs the structural tier. Set the gateway via env "
                "(MIRAGE_AGENT_API_KEY / _BASE_URL / _PROVIDER / _MODEL) or re-run "
                "with --no-agent for runtime-only."
            ),
        )
        return 1

    out_dir = pathlib.Path(args.out_dir)
    pipeline = Pipeline(output_base_dir=out_dir, config=fw, agent=agent)
    metrics = MetricsCollector(devkit_cmd=fw.devkit.devkit_cmd)
    # Sync the synthetic side's taskset pin to collection.yaml's mask.
    fw.devkit.cpu_range = cfg.cpu_mask

    def collect(binary: str, instr: dict[str, Any]) -> Profile | RunFailure:
        return cast(
            "Profile | RunFailure",
            collect_common.synthetic_collect(
                binary,
                instr,
                cfg=cfg,
                metrics=metrics,
                project_dir=pathlib.Path(binary).resolve().parent,
            ),
        )

    result = pipeline.run_iteration_loop(
        customer_profile=profile,
        seed_instruction=seed,
        sensitivity=sens,
        max_iter=args.max_iter,
        collect=collect,
        build=None,
    )

    assert result.history_path is not None, "pipeline must produce history_path"
    history = IterationHistory.load(pathlib.Path(result.history_path))
    _print_table(history)
    print(
        f"stop_reason={result.stop_reason} best_iteration={result.best_iteration} "
        f"total_iterations={history.total_iterations}"
    )

    # In-band diagnostic: no silent false success.
    if result.stop_reason == "converged" and history.total_iterations <= 1:
        print(
            "NOTE: seed was in-band (no steering needed); re-run with --threshold 5 "
            "to force iteration."
        )

    if args.no_agent:
        print("RUNTIME-ONLY")
        return 0

    dominant = DOMINANT[args.scenario]
    res = evaluate_criteria(history, result.stop_reason, dominant, args.threshold)
    names = ["triggered", "monotonic", "terminal", "non_dominant_cap"]
    print(
        f"{res.verdict}  " + ", ".join(f"{n}={c}" for n, c in zip(names, res.criteria, strict=True))
    )
    return 0 if res.verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
