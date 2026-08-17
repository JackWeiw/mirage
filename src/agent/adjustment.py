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
