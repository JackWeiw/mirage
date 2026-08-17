"""Tests for adjustment application (pure, no LLM/devkit)."""

from typing import Any

import pytest

from agent.adjustment import apply_adjustments


def _instr() -> dict[str, Any]:
    return {
        "project_name": "sim",
        "stages": [
            {
                "stage_name": "mem_stage",
                "implementation_strategy": "memory_synthesis",
                "strategies": [
                    {
                        "strategy": "memory_synthesis",
                        "synthesis_config": {"working_set_mb": 64, "access_pattern": "random"},
                    }
                ],
            },
            {
                "stage_name": "comp_stage",
                "implementation_strategy": "compute_synthesis",
                "strategies": [
                    {
                        "strategy": "compute_synthesis",
                        "synthesis_config": {"archetype": "matmul", "iterations": 100},
                    }
                ],
            },
        ],
        "config": {"compute_ratio": 0.5, "memory_ratio": 0.5, "thread_count": 4, "qps": 100},
    }


def test_apply_structural_knob_routes_to_synthesis_config() -> None:
    out = apply_adjustments(
        _instr(),
        [
            {
                "stage": "mem_stage",
                "knob": "working_set_mb",
                "from": 64,
                "to": 256,
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
    )
    assert out["stages"][0]["strategies"][0]["synthesis_config"]["working_set_mb"] == 256


def test_apply_structural_archetype_enum() -> None:
    out = apply_adjustments(
        _instr(),
        [
            {
                "stage": "comp_stage",
                "knob": "archetype",
                "from": "matmul",
                "to": "hash",
                "rationale": "",
                "expected_metric": "retiring",
                "expected_direction": "up",
            }
        ],
    )
    # stages[0] (mem_stage) is untouched — its synthesis_config has no archetype.
    assert "archetype" not in out["stages"][0]["strategies"][0]["synthesis_config"]
    assert out["stages"][0]["strategies"][0]["synthesis_config"]["access_pattern"] == "random"
    assert out["stages"][1]["strategies"][0]["synthesis_config"]["archetype"] == "hash"


def test_apply_runtime_knob_routes_to_config() -> None:
    out = apply_adjustments(
        _instr(),
        [
            {
                "stage": "",
                "knob": "compute_ratio",
                "from": 0.5,
                "to": 0.8,
                "rationale": "",
                "expected_metric": "retiring",
                "expected_direction": "up",
            }
        ],
    )
    assert out["config"]["compute_ratio"] == 0.8


def test_apply_unknown_knob_raises() -> None:
    with pytest.raises(ValueError, match="unknown knob"):
        apply_adjustments(
            _instr(),
            [
                {
                    "stage": "mem_stage",
                    "knob": "nope",
                    "from": 1,
                    "to": 2,
                    "rationale": "",
                    "expected_metric": "backend_bound",
                    "expected_direction": "up",
                }
            ],
        )


def test_apply_unknown_stage_raises() -> None:
    with pytest.raises(ValueError, match="unknown stage"):
        apply_adjustments(
            _instr(),
            [
                {
                    "stage": "ghost",
                    "knob": "working_set_mb",
                    "from": 64,
                    "to": 128,
                    "rationale": "",
                    "expected_metric": "backend_bound",
                    "expected_direction": "up",
                }
            ],
        )


def test_apply_runtime_knob_out_of_bounds_raises() -> None:
    with pytest.raises(ValueError, match="out of bounds"):
        apply_adjustments(
            _instr(),
            [
                {
                    "stage": "",
                    "knob": "compute_ratio",
                    "from": 0.5,
                    "to": 5.0,
                    "rationale": "",
                    "expected_metric": "retiring",
                    "expected_direction": "up",
                }
            ],
        )


def test_apply_archetype_invalid_enum_raises() -> None:
    with pytest.raises(ValueError, match="invalid value"):
        apply_adjustments(
            _instr(),
            [
                {
                    "stage": "comp_stage",
                    "knob": "archetype",
                    "from": "matmul",
                    "to": "bogus",
                    "rationale": "",
                    "expected_metric": "retiring",
                    "expected_direction": "up",
                }
            ],
        )
