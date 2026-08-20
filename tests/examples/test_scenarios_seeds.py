"""Seeds are out-of-band (~35pp off the dominant metric) and sensitivity loads via
the production load_sensitivity (exercising the expected->expected_direction rename)."""

import json
import pathlib

from agent.adjustment import load_sensitivity

_SCEN = pathlib.Path(__file__).resolve().parents[2] / "examples" / "scenarios"


def test_memory_bound_seed_is_compute_dominated() -> None:
    seed = json.loads((_SCEN / "memory_bound" / "seed_instruction.json").read_text())
    assert seed["config"]["memory_ratio"] == 0.2
    assert seed["config"]["compute_ratio"] == 0.8
    mem = next(s for s in seed["stages"] if s["stage_name"] == "mem_stage")
    assert mem["strategies"][0]["synthesis_config"]["working_set_mb"] == 16
    assert mem["strategies"][0]["synthesis_config"]["access_pattern"] == "sequential"


def test_memory_bound_sensitivity_loads_with_rename() -> None:
    table = load_sensitivity(_SCEN / "memory_bound" / "sensitivity.json")
    assert table["working_set_mb"]["target_metric"] == "backend_bound"
    # the loader renames on-disk "expected" -> in-memory "expected_direction"
    assert table["working_set_mb"]["expected_direction"] == "up"
    assert table["working_set_mb"]["verdict"] == "controllable"
