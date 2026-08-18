"""Deterministic adjustment mechanics for the Phase 2 auto-iteration loop.

Pure functions only — no LLM, no devkit, no I/O except apply_adjustments_to_config.
The loop driver (PR 3) wires these into run_iteration_loop.
"""

import json
import logging
import pathlib
from copy import deepcopy
from typing import Any

_log = logging.getLogger(__name__)

# --- Named knob space (RFC 0003 §1) + valid domains ---

# Structural knobs mutate stages[i].strategies[0].synthesis_config[knob].
STRUCTURAL_KNOBS: dict[str, dict[str, Any]] = {
    "working_set_mb": {"kind": "int", "min": 1, "max": 4096},
    "access_pattern": {"kind": "enum", "values": ("sequential", "mixed", "random")},
    "iterations": {"kind": "int", "min": 1, "max": 1_000_000},
    "archetype": {"kind": "enum", "values": ("compute", "hash", "matmul", "sort", "branch")},
}
# Runtime knobs mutate instruction["config"][knob].
RUNTIME_KNOBS: dict[str, dict[str, Any]] = {
    "compute_ratio": {"kind": "float", "min": 0.0, "max": 1.0},
    "memory_ratio": {"kind": "float", "min": 0.0, "max": 1.0},
    "thread_count": {"kind": "int", "min": 1, "max": 1024},
    "qps": {"kind": "int", "min": 1, "max": 1_000_000},
}
KNOB_DOMAINS: dict[str, dict[str, Any]] = {**STRUCTURAL_KNOBS, **RUNTIME_KNOBS}


def _validate_value(knob: str, to: Any) -> None:
    """Raise ValueError if `to` is outside the knob's valid domain."""
    if knob not in KNOB_DOMAINS:
        raise ValueError(f"unknown knob: {knob}")
    dom = KNOB_DOMAINS[knob]
    if dom["kind"] == "enum":
        if to not in dom["values"]:
            raise ValueError(f"invalid value {to!r} for {knob}; not in {dom['values']}")
    elif dom["kind"] in ("int", "float"):
        if not isinstance(to, int | float) or isinstance(to, bool):
            raise ValueError(f"{knob} must be a number, got {type(to).__name__}")
        if dom["kind"] == "int" and not float(to).is_integer():
            raise ValueError(f"{knob} must be an integer, got {to}")
        if not (dom["min"] <= to <= dom["max"]):
            raise ValueError(f"{knob}={to} out of bounds [{dom['min']}, {dom['max']}]")
    else:  # pragma: no cover - defensive
        raise ValueError(f"unknown domain kind for {knob}")


def apply_adjustments(
    instruction: dict[str, Any], adjustments: list[dict[str, Any]]
) -> dict[str, Any]:
    """Apply a list of adjustments to a copy of the instruction.

    Structural knobs route to stages[i].strategies[0].synthesis_config (matched
    by stage_name); runtime knobs route to instruction["config"][knob]. Raises
    on unknown stage/knob or out-of-domain `to`.
    """
    out = deepcopy(instruction)
    for adj in adjustments:
        knob = adj["knob"]
        _validate_value(knob, adj["to"])
        if knob in RUNTIME_KNOBS:
            out.setdefault("config", {})[knob] = adj["to"]
        else:
            stage_name = adj.get("stage", "")
            stage = next(
                (s for s in out.get("stages", []) if s.get("stage_name") == stage_name), None
            )
            if stage is None:
                raise ValueError(f"unknown stage: {stage_name!r}")
            stage["strategies"][0]["synthesis_config"][knob] = adj["to"]
    return out


def apply_adjustments_to_config(
    config_path: pathlib.Path, adjustments: list[dict[str, Any]]
) -> None:
    """Read config.json, apply the runtime-knob subset of adjustments, write back.

    Structural knobs are skipped (they live in synthesis_config, not config.json).
    NOTE: this is a plain (non-atomic) writer used by PR 1 tooling/tests; the
    loop driver (PR 3) uses write_config_json_atomic for the crash-safe path.
    """
    data = json.loads(config_path.read_text())
    for adj in adjustments:
        knob = adj["knob"]
        if knob not in RUNTIME_KNOBS:
            continue  # structural knobs are not config.json entries
        _validate_value(knob, adj["to"])
        data[knob] = adj["to"]
    config_path.write_text(json.dumps(data, indent=2))


def load_sensitivity(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    """Load the spike's sensitivity.json verdicts into a per-knob table.

    The spike writes each verdict with an `expected` field ("up"/"down"); this
    loader renames it to `expected_direction` (the name the controller / gate
    consult). A dead knob keeps whatever `expected` value the spike wrote
    (often the literal string `"dead"`, or `None` when the key is absent).
    """
    raw = json.loads(path.read_text())
    verdicts = raw if isinstance(raw, list) else raw.get("verdicts", [])
    table: dict[str, dict[str, Any]] = {}
    for v in verdicts:
        knob = v["knob"]
        table[knob] = {
            "target_metric": v.get("target_metric"),
            "expected_direction": v.get("expected"),
            "verdict": v.get("verdict", "dead"),
            "values": v.get("values", []),
            "metric_values": v.get("metric_values", []),
        }
    return table


def _actual_current(instruction: dict[str, Any], adj: dict[str, Any]) -> Any:
    """Read the knob's actual current value from the instruction."""
    knob = adj["knob"]
    if knob in RUNTIME_KNOBS:
        return instruction.get("config", {}).get(knob)
    stage_name = adj.get("stage", "")
    stage = next(
        (s for s in instruction.get("stages", []) if s.get("stage_name") == stage_name), None
    )
    if stage is None:
        return None
    return stage.get("strategies", [{}])[0].get("synthesis_config", {}).get(knob)


def _error_sign(report: dict[str, Any], metric: str, threshold: float) -> int:
    """+1 if workload metric is too high (diff_pct>0 beyond threshold), -1 too low, 0 satisfied."""
    diff = report.get("topdown_l1", {}).get(metric, {}).get("diff_pct", 0.0)
    if abs(diff) <= threshold:
        return 0
    return 1 if diff > 0 else -1


def validate_adjustments(
    adjustments: list[dict[str, Any]],
    instruction: dict[str, Any],
    report: dict[str, Any],
    sensitivity: dict[str, dict[str, Any]],
    tier: str,
    topdown_threshold_pct: float = 10.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Gate every adjustment list (deterministic or LLM) before apply_adjustments.

    Returns (accepted, rejected). Each rejected carries a `reason`.
    See spec §1b: domain, tier-ownership, direction (decoupled from largest
    error), from-mismatch (warn only).
    """
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for adj in adjustments:
        knob = adj["knob"]
        # Domain.
        if knob not in KNOB_DOMAINS:
            rejected.append({**adj, "reason": "unknown_knob"})
            continue
        try:
            _validate_value(knob, adj["to"])
        except ValueError as exc:
            rejected.append({**adj, "reason": f"domain_violation:{exc}"})
            continue

        # Tier ownership.
        is_runtime = knob in RUNTIME_KNOBS
        if tier == "structural" and is_runtime:
            rejected.append({**adj, "reason": "runtime_knob_not_owned_on_structural_tier"})
            continue
        if tier == "runtime" and not is_runtime:
            rejected.append({**adj, "reason": "structural_knob_not_owned_on_runtime_tier"})
            continue

        # Direction (decoupled from largest error): uses ACTUAL current, not from.
        actual = _actual_current(instruction, adj)
        to = adj["to"]
        entry = sensitivity.get(knob, {})
        metric = adj.get("expected_metric") or entry.get("target_metric")
        direction = adj.get("expected_direction") or entry.get("expected_direction")
        if metric is None or direction is None:
            rejected.append({**adj, "reason": "no_sensitivity_entry"})
            continue

        err = _error_sign(report, metric, topdown_threshold_pct)
        if err == 0:
            rejected.append({**adj, "reason": "metric_already_satisfied"})
            continue

        # The sign-based direction check applies only to numeric knobs. A missing
        # actual (knob absent from the instruction), a non-numeric actual (enum
        # knobs like archetype/access_pattern whose direction cannot be sign-
        # verified), and a no-op (to == actual) each get a distinct reason so an
        # operator can triage rather than chase a phantom wrong-direction. Full
        # enum direction verification (per-value metric comparison from the spike
        # data) is deferred to the LLM structural tier.
        if actual is None:
            rejected.append({**adj, "reason": "missing_actual"})
            continue
        if not isinstance(to, int | float) or not isinstance(actual, int | float):
            rejected.append({**adj, "reason": "non_numeric_knob_direction_uncheckable"})
            continue

        # Want to move metric DOWN (err=+1, too high) or UP (err=-1, too low).
        want_down = err > 0
        # direction "up" means knob raises metric; "down" means lowers it.
        knob_raises = direction == "up"
        # Required knob move sign to reduce error:
        #   want metric down + knob raises -> decrease knob (to<actual)
        #   want metric down + knob lowers  -> increase knob
        #   want metric up   + knob raises -> increase knob
        #   want metric up   + knob lowers  -> decrease knob
        want_increase = want_down != knob_raises  # XOR
        move_sign = to - actual
        if move_sign == 0:
            rejected.append({**adj, "reason": "no_op_move"})
            continue
        move_up = move_sign > 0
        if (want_increase and not move_up) or (not want_increase and move_up):
            rejected.append({**adj, "reason": "wrong_direction"})
            continue

        # from-mismatch: warn only.
        if adj.get("from") is not None and adj["from"] != actual:
            _log.warning(
                "adjustment from-mismatch: knob=%s from=%s actual=%s to=%s",
                knob,
                adj["from"],
                actual,
                to,
            )

        accepted.append(adj)

    return accepted, rejected


# Bounded steps per runtime knob (spec §3).
_STEP: dict[str, float] = {
    "compute_ratio": 0.2,
    "memory_ratio": 0.2,
    "thread_count": 1.0,
    "qps": 20.0,
}


def deterministic_revise(
    instruction: dict[str, Any],
    report: dict[str, Any],
    sensitivity: dict[str, dict[str, Any]],
    history: Any,
    oscillation_window: int = 3,
    topdown_threshold_pct: float = 10.0,
) -> list[dict[str, Any]]:
    """Assist controller: emit ONE runtime-knob adjustment that reduces an
    out-of-threshold metric. Pure, no LLM.

    Walks the candidate metrics largest-error-first and returns the first
    steerable one (controllable runtime knob, not skip-blocked, not
    boundary-exhausted). Falls through to the next-largest metric when the
    largest has no steerable knob (#56). Returns [] only when every candidate
    metric is unsteerable / skip-blocked / boundary-exhausted -> forces
    escalation to the LLM tier.
    """
    # Largest-error topdown metric not within threshold.
    topdown = report.get("topdown_l1", {})
    candidates = [
        (m, abs(v.get("diff_pct", 0.0)))
        for m, v in topdown.items()
        if abs(v.get("diff_pct", 0.0)) > topdown_threshold_pct
    ]
    if not candidates:
        return []
    candidates.sort(key=lambda x: (-x[1], x[0]))  # largest error first; name breaks ties

    # Knobs toggled within the window are skip-blocked.
    recent_moves: set[str] = set()
    for r in getattr(history, "records", [])[-oscillation_window:]:
        for mv in getattr(r, "applied_moves", []):
            recent_moves.add(str(mv["knob"]))

    # Fall through the candidate metrics largest-first: the largest-error metric
    # may have no steerable runtime knob (e.g. frontend_bound has none) or may be
    # boundary-exhausted, in which case we try the next-largest metric instead of
    # dead-ending on iter 0 (#56). Returns [] only when every candidate metric is
    # unsteerable / skip-blocked / boundary-exhausted.
    for target_metric, _ in candidates:
        err = _error_sign(report, target_metric, topdown_threshold_pct)
        if err == 0:
            continue
        want_down = err > 0

        # Find a runtime knob whose expected_direction reduces this metric.
        for knob, entry in sensitivity.items():
            if knob not in RUNTIME_KNOBS:
                continue
            if entry.get("verdict") != "controllable":
                continue
            if entry.get("target_metric") != target_metric:
                continue
            if knob in recent_moves:
                continue  # skip-blocked
            direction = entry.get("expected_direction")
            if direction not in ("up", "down"):
                continue
            knob_raises = direction == "up"
            want_increase = want_down != knob_raises  # XOR (same as the gate)
            actual = instruction.get("config", {}).get(knob)
            if not isinstance(actual, int | float) or isinstance(actual, bool):
                continue
            step = _STEP.get(knob, 0.1)
            to = actual + step if want_increase else actual - step
            dom = KNOB_DOMAINS[knob]
            to = max(dom["min"], min(dom["max"], to))  # clamp
            if to == actual:
                continue  # at boundary, can't move in the wanted direction
            return [
                {
                    "stage": "",
                    "knob": knob,
                    "from": actual,
                    "to": to,
                    "rationale": f"{target_metric} diff out of threshold; {direction} via {knob}",
                    "expected_metric": target_metric,
                    "expected_direction": direction,
                }
            ]
    return []
