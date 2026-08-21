"""Tests for adjustment application (pure, no LLM/devkit)."""

import json
import pathlib
from typing import Any

import pytest

from agent.adjustment import apply_adjustments
from observability.iteration_history import IterationHistory, IterationRecord


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


def test_apply_does_not_mutate_input() -> None:
    import copy

    instr = _instr()
    snapshot = copy.deepcopy(instr)
    apply_adjustments(
        instr,
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
    assert instr == snapshot  # input instruction untouched (deepcopy guarantee)


def test_validate_rejects_bool_for_numeric_knob() -> None:
    # bool is a subclass of int in Python; the guard must reject True/False as a number.
    with pytest.raises(ValueError, match="must be a number"):
        apply_adjustments(
            _instr(),
            [
                {
                    "stage": "",
                    "knob": "thread_count",
                    "from": 4,
                    "to": True,
                    "rationale": "",
                    "expected_metric": "backend_bound",
                    "expected_direction": "up",
                }
            ],
        )


def test_apply_adjustments_to_config_writes_runtime_subset(tmp_path: pathlib.Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"compute_ratio": 0.5, "memory_ratio": 0.5, "thread_count": 4, "qps": 100})
    )
    from agent.adjustment import apply_adjustments_to_config

    apply_adjustments_to_config(
        cfg_path,
        [
            {
                "knob": "compute_ratio",
                "from": 0.5,
                "to": 0.8,
                "rationale": "",
                "expected_metric": "retiring",
                "expected_direction": "up",
            }
        ],
    )
    data = json.loads(cfg_path.read_text())
    assert data["compute_ratio"] == 0.8
    assert data["memory_ratio"] == 0.5  # untouched


def test_apply_adjustments_to_config_ignores_structural(tmp_path: pathlib.Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"compute_ratio": 0.5}))
    from agent.adjustment import apply_adjustments_to_config

    apply_adjustments_to_config(
        cfg_path,
        [
            {
                "knob": "working_set_mb",
                "from": 64,
                "to": 256,
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
    )
    # structural knob silently skipped (it doesn't live in config.json)
    assert json.loads(cfg_path.read_text()) == {"compute_ratio": 0.5}


def test_apply_adjustments_to_config_aborts_on_invalid_keeping_file(tmp_path: pathlib.Path) -> None:
    cfg_path = tmp_path / "config.json"
    original = {"compute_ratio": 0.5, "thread_count": 4}
    cfg_path.write_text(json.dumps(original))
    from agent.adjustment import apply_adjustments_to_config

    with pytest.raises(ValueError):
        apply_adjustments_to_config(
            cfg_path,
            [
                {
                    "knob": "compute_ratio",
                    "from": 0.5,
                    "to": 0.8,
                    "rationale": "",
                    "expected_metric": "retiring",
                    "expected_direction": "up",
                },
                {
                    "knob": "thread_count",
                    "from": 4,
                    "to": -1,
                    "rationale": "",
                    "expected_metric": "backend_bound",
                    "expected_direction": "up",
                },  # out of bounds (min=1)
            ],
        )
    # The on-disk file is unchanged — the first (valid) adjustment was NOT persisted.
    assert json.loads(cfg_path.read_text()) == original


def test_load_sensitivity_renames_expected_field() -> None:
    from agent.adjustment import load_sensitivity

    table = load_sensitivity(
        pathlib.Path(__file__).parent.parent / "data" / "sensitivity_sample.json"
    )
    e = table["working_set_mb"]
    assert e["target_metric"] == "backend_bound"
    assert e["expected_direction"] == "up"  # renamed from spike's "expected"
    assert e["verdict"] == "controllable"
    assert e["metric_values"] == [12.06, 15.28, 16.41]


def test_load_sensitivity_dead_knob_kept() -> None:
    from agent.adjustment import load_sensitivity

    table = load_sensitivity(
        pathlib.Path(__file__).parent.parent / "data" / "sensitivity_sample.json"
    )
    assert table["thread_count"]["verdict"] == "dead"
    assert table["thread_count"]["expected_direction"] == "dead"


def test_load_sensitivity_bare_list_shape(tmp_path: pathlib.Path) -> None:
    """The spike may emit a bare JSON list of verdicts (no wrapping object)."""
    from agent.adjustment import load_sensitivity

    verdicts = [
        {
            "knob": "working_set_mb",
            "target_metric": "backend_bound",
            "expected": "up",
            "verdict": "controllable",
            "values": [16, 64, 256],
            "metric_values": [12.06, 15.28, 16.41],
        },
        {
            "knob": "archetype",
            "target_metric": "retiring",
            "expected": "up",
            "verdict": "controllable",
            "values": ["compute", "hash", "matmul"],
            "metric_values": [22.87, 21.33, 63.91],
        },
    ]
    p = tmp_path / "bare.json"
    p.write_text(json.dumps(verdicts))
    table = load_sensitivity(p)
    assert len(table) == 2
    assert table["working_set_mb"]["expected_direction"] == "up"
    assert table["archetype"]["expected_direction"] == "up"


def _report(backend_diff: float = 0.0, retiring_diff: float = 0.0) -> dict[str, Any]:
    """A fake comparator report. diff_pct>0 means workload TOO HIGH vs customer."""
    return {
        "topdown_l1": {
            "backend_bound": {
                "diff_pct": backend_diff,
                "within_threshold": abs(backend_diff) <= 10.0,
            },
            "retiring": {"diff_pct": retiring_diff, "within_threshold": abs(retiring_diff) <= 10.0},
            "frontend_bound": {"diff_pct": 0.0, "within_threshold": True},
            "bad_speculation": {"diff_pct": 0.0, "within_threshold": True},
        },
        "memory": {"bandwidth_gbps": {"diff_pct": 0.0, "within_threshold": True}},
        "hotspot_coverage": {"coverage_pct": 85.0},
        "convergence": {"converged": False, "reason": ""},
    }


_SENS = {
    "compute_ratio": {
        "target_metric": "retiring",
        "expected_direction": "up",
        "verdict": "controllable",
    },
    "memory_ratio": {
        "target_metric": "backend_bound",
        "expected_direction": "up",
        "verdict": "controllable",
    },
    "working_set_mb": {
        "target_metric": "backend_bound",
        "expected_direction": "up",
        "verdict": "controllable",
    },
}


def test_validate_wrong_direction_rejected() -> None:
    # backend_bound diff = +20 (too high). memory_ratio is an "up" knob on backend_bound,
    # so to LOWER backend we must DECREASE memory_ratio. Increasing it is wrong direction.
    instr = {"config": {"memory_ratio": 0.5}}
    from agent.adjustment import validate_adjustments

    accepted, rejected = validate_adjustments(
        [
            {
                "stage": "",
                "knob": "memory_ratio",
                "from": 0.5,
                "to": 0.8,
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        _SENS,
        tier="runtime",
    )
    assert accepted == []
    assert rejected[0]["reason"] == "wrong_direction"


def test_validate_correct_direction_accepted() -> None:
    instr = {"config": {"memory_ratio": 0.5}}
    from agent.adjustment import validate_adjustments

    accepted, rejected = validate_adjustments(
        [
            {
                "stage": "",
                "knob": "memory_ratio",
                "from": 0.5,
                "to": 0.2,
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        _SENS,
        tier="runtime",
    )
    assert len(accepted) == 1
    assert rejected == []


def test_validate_already_satisfied_rejected() -> None:
    # backend within threshold -> adjusting it is pointless churn.
    instr = {"config": {"memory_ratio": 0.5}}
    from agent.adjustment import validate_adjustments

    accepted, rejected = validate_adjustments(
        [
            {
                "stage": "",
                "knob": "memory_ratio",
                "from": 0.5,
                "to": 0.2,
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=2.0),
        _SENS,
        tier="runtime",
    )
    assert accepted == []
    assert rejected[0]["reason"] == "metric_already_satisfied"


def test_validate_secondary_metric_accepted() -> None:
    # backend is the LARGEST error (+20) but the adjustment targets retiring (also
    # unsatisfied, -15 = too low). retiring is "up"; workload too low -> increase.
    # The gate must ACCEPT this secondary-metric move (not largest-error coupled).
    instr = {"config": {"compute_ratio": 0.5}}
    from agent.adjustment import validate_adjustments

    accepted, _ = validate_adjustments(
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
        instr,
        _report(backend_diff=20.0, retiring_diff=-15.0),
        _SENS,
        tier="runtime",
    )
    assert len(accepted) == 1


def test_validate_tier_ownership_drops_runtime_on_structural() -> None:
    instr = {"config": {"memory_ratio": 0.5}, "stages": []}
    from agent.adjustment import validate_adjustments

    accepted, rejected = validate_adjustments(
        [
            {
                "stage": "",
                "knob": "memory_ratio",
                "from": 0.5,
                "to": 0.2,
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        _SENS,
        tier="structural",
    )
    assert accepted == []
    assert rejected[0]["reason"] == "runtime_knob_not_owned_on_structural_tier"


def test_validate_tier_ownership_drops_structural_on_runtime() -> None:
    instr = {
        "stages": [
            {
                "stage_name": "mem_stage",
                "strategies": [{"synthesis_config": {"working_set_mb": 64}}],
            }
        ]
    }
    from agent.adjustment import validate_adjustments

    accepted, rejected = validate_adjustments(
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
        instr,
        _report(backend_diff=20.0),
        _SENS,
        tier="runtime",
    )
    assert accepted == []
    assert rejected[0]["reason"] == "structural_knob_not_owned_on_runtime_tier"


def test_validate_from_mismatch_warns_but_keeps() -> None:
    # from=0.2 but actual=0.5; the gate re-derives 0.5->0.2 (correct direction) and accepts.
    instr = {"config": {"memory_ratio": 0.5}}
    from agent.adjustment import validate_adjustments

    accepted, _rejected = validate_adjustments(
        [
            {
                "stage": "",
                "knob": "memory_ratio",
                "from": 0.2,
                "to": 0.2,
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        _SENS,
        tier="runtime",
    )
    assert len(accepted) == 1  # accepted despite stale from
    # (warning is logged, not returned; not assertable here without a log spy)


def test_validate_all_rejected_returns_empty_accepted() -> None:
    instr = {"config": {"memory_ratio": 0.5}}
    from agent.adjustment import validate_adjustments

    accepted, rejected = validate_adjustments(
        [
            {
                "stage": "",
                "knob": "memory_ratio",
                "from": 0.5,
                "to": 0.8,
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        _SENS,
        tier="runtime",
    )
    assert accepted == []
    assert len(rejected) == 1


def test_validate_unknown_knob_rejected() -> None:
    instr = {"config": {"memory_ratio": 0.5}}
    from agent.adjustment import validate_adjustments

    accepted, rejected = validate_adjustments(
        [
            {
                "stage": "",
                "knob": "bogus_knob",
                "from": 1,
                "to": 2,
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        _SENS,
        tier="runtime",
    )
    assert accepted == []
    assert rejected[0]["reason"] == "unknown_knob"


def test_validate_domain_violation_rejected() -> None:
    instr = {"config": {"memory_ratio": 0.5}}
    from agent.adjustment import validate_adjustments

    accepted, rejected = validate_adjustments(
        [
            {
                "stage": "",
                "knob": "memory_ratio",
                "from": 0.5,
                "to": "not_a_number",
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        _SENS,
        tier="runtime",
    )
    assert accepted == []
    assert rejected[0]["reason"].startswith("domain_violation:")


def test_validate_no_sensitivity_entry_rejected() -> None:
    # thread_count is a valid runtime knob but has no entry in _SENS, and the adj
    # carries no expected_metric/expected_direction of its own.
    instr = {"config": {"thread_count": 4}}
    from agent.adjustment import validate_adjustments

    accepted, rejected = validate_adjustments(
        [
            {
                "stage": "",
                "knob": "thread_count",
                "from": 4,
                "to": 8,
                "rationale": "",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        _SENS,
        tier="runtime",
    )
    assert accepted == []
    assert rejected[0]["reason"] == "no_sensitivity_entry"


def test_validate_missing_actual_rejected() -> None:
    # memory_ratio is ABSENT from the instruction's config, so _actual_current returns None.
    instr: dict[str, Any] = {"config": {}}
    from agent.adjustment import validate_adjustments

    accepted, rejected = validate_adjustments(
        [
            {
                "stage": "",
                "knob": "memory_ratio",
                "from": 0.5,
                "to": 0.2,
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        _SENS,
        tier="runtime",
    )
    assert accepted == []
    assert rejected[0]["reason"] == "missing_actual"


class _LogSpy:
    """Records structlog-style info/warning calls (structlog prints to stdout,
    so pytest caplog cannot capture it; monkeypatch the module logger instead)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def _record(self, event: str | None, kw: dict[str, Any]) -> None:
        self.events.append((event or "", kw))

    def info(self, event: str | None = None, **kw: Any) -> None:
        self._record(event, kw)

    def warning(self, event: str | None = None, **kw: Any) -> None:
        self._record(event, kw)

    def debug(self, event: str | None = None, **kw: Any) -> None:
        self._record(event, kw)


def test_validate_enum_no_spike_data_trusted_on_structural(monkeypatch: Any) -> None:
    # archetype has no sensitivity entry, but the adj declares an unsatisfied
    # target metric (retiring too low -> want up). The LLM structural tier is
    # the designated owner of un-instrumented structural revision -> trust it.
    spy = _LogSpy()
    monkeypatch.setattr("agent.adjustment.logger", spy)
    instr = {
        "stages": [
            {
                "stage_name": "comp",
                "strategies": [{"synthesis_config": {"archetype": "hash"}}],
            }
        ]
    }
    from agent.adjustment import validate_adjustments

    accepted, rejected = validate_adjustments(
        [
            {
                "stage": "comp",
                "knob": "archetype",
                "from": "hash",
                "to": "matmul",
                "rationale": "",
                "expected_metric": "retiring",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(retiring_diff=-15.0),
        _SENS,  # archetype absent -> no spike data
        tier="structural",
    )
    assert len(accepted) == 1
    assert rejected == []
    assert spy.events, "expected a structural_enum_trusted log event"
    trusted = [e for e in spy.events if e[0] == "structural_enum_trusted"]
    assert len(trusted) == 1
    fields = trusted[0][1]
    assert fields["knob"] == "archetype"
    assert fields["actual"] == "hash"
    assert fields["to"] == "matmul"
    assert fields["metric"] == "retiring"


def test_validate_logs_adjustment_proposed_at_debug(monkeypatch: Any) -> None:
    # Every proposal that reaches direction evaluation is DEBUG-logged with its
    # full direction context, so an operator running at MIRAGE_LOG_LEVEL=DEBUG
    # can verify a reject reason (e.g. wrong_direction) against the proven
    # direction + actual value. Here a memory_ratio move that goes the WRONG
    # way is both logged (proposal + context) and rejected (wrong_direction) --
    # the exact pairing an operator triages on a real run.
    spy = _LogSpy()
    monkeypatch.setattr("agent.adjustment.logger", spy)
    instr = {"config": {"memory_ratio": 0.5}}
    from agent.adjustment import validate_adjustments

    accepted, rejected = validate_adjustments(
        [
            {
                "stage": "",
                "knob": "memory_ratio",
                "from": 0.5,
                "to": 0.6,  # increase, but backend too high wants decrease -> wrong_direction
                "rationale": "bump memory",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),  # backend too high -> want down (err=+1)
        _SENS,
        tier="runtime",
    )
    assert accepted == []
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "wrong_direction"
    # The proposal was DEBUG-logged before the reject, with the context needed
    # to confirm the reject is genuine (direction + actual + err sign).
    proposed = [e for e in spy.events if e[0] == "adjustment_proposed"]
    assert len(proposed) == 1
    fields = proposed[0][1]
    assert fields["knob"] == "memory_ratio"
    assert fields["from_"] == 0.5
    assert fields["to"] == 0.6
    assert fields["metric"] == "backend_bound"
    assert fields["direction"] == "up"
    assert fields["actual"] == 0.5
    assert fields["err"] == 1  # backend too high -> want down
    assert fields["tier"] == "runtime"


def test_validate_enum_with_spike_data_correct_direction_accepted() -> None:
    # access_pattern targets backend_bound (up): [sequential,mixed,random] -> [30,50,70].
    # backend too high (+20, want down) -> moving random->sequential reduces it -> accept.
    instr = {
        "stages": [
            {
                "stage_name": "mem",
                "strategies": [{"synthesis_config": {"access_pattern": "random"}}],
            }
        ]
    }
    sens = {
        "access_pattern": {
            "target_metric": "backend_bound",
            "expected_direction": "up",
            "verdict": "controllable",
            "values": ["sequential", "mixed", "random"],
            "metric_values": [30.0, 50.0, 70.0],
        }
    }
    from agent.adjustment import validate_adjustments

    accepted, rejected = validate_adjustments(
        [
            {
                "stage": "mem",
                "knob": "access_pattern",
                "from": "random",
                "to": "sequential",
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        sens,
        tier="structural",
    )
    assert len(accepted) == 1
    assert rejected == []


def test_validate_enum_with_spike_data_wrong_direction_rejected() -> None:
    # Same setup, but moving sequential->random INCREASES backend (wrong direction).
    instr = {
        "stages": [
            {
                "stage_name": "mem",
                "strategies": [{"synthesis_config": {"access_pattern": "sequential"}}],
            }
        ]
    }
    sens = {
        "access_pattern": {
            "target_metric": "backend_bound",
            "expected_direction": "up",
            "verdict": "controllable",
            "values": ["sequential", "mixed", "random"],
            "metric_values": [30.0, 50.0, 70.0],
        }
    }
    from agent.adjustment import validate_adjustments

    accepted, rejected = validate_adjustments(
        [
            {
                "stage": "mem",
                "knob": "access_pattern",
                "from": "sequential",
                "to": "random",
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        sens,
        tier="structural",
    )
    assert accepted == []
    assert rejected[0]["reason"] == "wrong_direction"


def test_validate_no_op_move_rejected() -> None:
    # to == actual (0.5 -> 0.5) is a no-op move; distinct from wrong_direction.
    instr = {"config": {"memory_ratio": 0.5}}
    from agent.adjustment import validate_adjustments

    accepted, rejected = validate_adjustments(
        [
            {
                "stage": "",
                "knob": "memory_ratio",
                "from": 0.5,
                "to": 0.5,
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        _SENS,
        tier="runtime",
    )
    assert accepted == []
    assert rejected[0]["reason"] == "no_op_move"


def test_deterministic_revise_picks_correct_runtime_knob_direction() -> None:
    # backend_bound too high (+20); memory_ratio is "up" on backend -> must DECREASE it.
    instr = {"config": {"compute_ratio": 0.5, "memory_ratio": 0.5, "thread_count": 4, "qps": 100}}
    from agent.adjustment import deterministic_revise

    adj = deterministic_revise(
        instr, _report(backend_diff=20.0), _SENS, IterationHistory(customer_name="t")
    )
    assert len(adj) == 1
    assert adj[0]["knob"] == "memory_ratio"
    assert adj[0]["to"] < 0.5  # decreased
    assert adj[0]["from"] == 0.5  # from == actual current


def test_deterministic_revise_boundary_exhausted_returns_empty() -> None:
    # memory_ratio already at min 0.0 and backend too high -> can't decrease further;
    # no other runtime knob targets backend_bound -> escalate (return []).
    instr = {"config": {"compute_ratio": 0.5, "memory_ratio": 0.0, "thread_count": 4, "qps": 100}}
    from agent.adjustment import deterministic_revise

    adj = deterministic_revise(
        instr, _report(backend_diff=20.0), _SENS, IterationHistory(customer_name="t")
    )
    assert adj == []


def test_deterministic_revise_never_emits_structural() -> None:
    instr = {"config": {"compute_ratio": 0.5, "memory_ratio": 0.5, "thread_count": 4, "qps": 100}}
    from agent.adjustment import deterministic_revise

    adj = deterministic_revise(
        instr, _report(backend_diff=20.0), _SENS, IterationHistory(customer_name="t")
    )
    for a in adj:
        assert a["knob"] in ("compute_ratio", "memory_ratio", "thread_count", "qps")


def test_deterministic_revise_skip_blocked_returns_empty() -> None:
    # If the only candidate knob was toggled within the oscillation window, return [].
    instr = {"config": {"compute_ratio": 0.5, "memory_ratio": 0.5, "thread_count": 4, "qps": 100}}
    hist = IterationHistory(customer_name="t")
    hist.add_record(
        IterationRecord(
            iteration=1,
            converged=False,
            topdown_diffs={"backend_bound": 20.0},
            applied_moves=[{"knob": "memory_ratio", "tier": "runtime", "sign": -1}],
        )
    )
    from agent.adjustment import deterministic_revise

    adj = deterministic_revise(instr, _report(backend_diff=20.0), _SENS, hist, oscillation_window=3)
    assert adj == []  # memory_ratio skip-blocked; no other runtime knob targets backend


def test_deterministic_revise_no_unsatisfied_metric_returns_empty() -> None:
    # All metrics within threshold -> nothing to fix.
    instr = {"config": {"compute_ratio": 0.5, "memory_ratio": 0.5, "thread_count": 4, "qps": 100}}
    from agent.adjustment import deterministic_revise

    adj = deterministic_revise(
        instr, _report(backend_diff=2.0), _SENS, IterationHistory(customer_name="t")
    )
    assert adj == []  # backend within threshold (2.0 <= 10.0)


def test_deterministic_revise_no_runtime_knob_targets_metric_returns_empty() -> None:
    # bad_speculation has the largest error but _SENS has no runtime knob targeting it
    # -> the deterministic tier can't help -> escalate (return []).
    instr = {"config": {"compute_ratio": 0.5, "memory_ratio": 0.5, "thread_count": 4, "qps": 100}}
    report = {
        "topdown_l1": {
            "backend_bound": {"diff_pct": 0.0, "within_threshold": True},
            "retiring": {"diff_pct": 0.0, "within_threshold": True},
            "frontend_bound": {"diff_pct": 0.0, "within_threshold": True},
            "bad_speculation": {"diff_pct": 20.0, "within_threshold": False},
        },
        "memory": {"bandwidth_gbps": {"diff_pct": 0.0, "within_threshold": True}},
        "hotspot_coverage": {"coverage_pct": 85.0},
        "convergence": {"converged": False, "reason": ""},
    }
    from agent.adjustment import deterministic_revise

    adj = deterministic_revise(instr, report, _SENS, IterationHistory(customer_name="t"))
    assert adj == []


def test_deterministic_revise_falls_through_unsteerable_largest_metric() -> None:
    # Largest error is frontend_bound (no runtime knob targets it); the fix must
    # fall through to the next-largest steerable metric (backend_bound) instead
    # of dead-ending on iter 0 (issue #56).
    instr = {"config": {"compute_ratio": 0.5, "memory_ratio": 0.5, "thread_count": 4, "qps": 100}}
    report = {
        "topdown_l1": {
            "backend_bound": {"diff_pct": 20.0, "within_threshold": False},
            "retiring": {"diff_pct": 0.0, "within_threshold": True},
            "frontend_bound": {"diff_pct": 30.0, "within_threshold": False},
            "bad_speculation": {"diff_pct": 0.0, "within_threshold": True},
        },
        "memory": {"bandwidth_gbps": {"diff_pct": 0.0, "within_threshold": True}},
        "hotspot_coverage": {"coverage_pct": 85.0},
        "convergence": {"converged": False, "reason": ""},
    }
    from agent.adjustment import deterministic_revise

    adj = deterministic_revise(instr, report, _SENS, IterationHistory(customer_name="t"))
    assert len(adj) == 1
    assert adj[0]["knob"] == "memory_ratio"  # targets backend_bound (the steerable one)
    assert adj[0]["expected_metric"] == "backend_bound"
    assert adj[0]["to"] < 0.5  # backend too high + memory_ratio "up" => decrease


def test_deterministic_revise_falls_through_boundary_exhausted_metric() -> None:
    # Largest metric (backend_bound) IS steerable but its only runtime knob
    # (memory_ratio) is at the boundary (min 0.0) and can't move down; the fix
    # must fall through to the next steerable metric (retiring via compute_ratio)
    # instead of dead-ending (issue #56).
    instr = {"config": {"compute_ratio": 0.5, "memory_ratio": 0.0, "thread_count": 4, "qps": 100}}
    report = {
        "topdown_l1": {
            "backend_bound": {"diff_pct": 30.0, "within_threshold": False},
            "retiring": {"diff_pct": 20.0, "within_threshold": False},
            "frontend_bound": {"diff_pct": 0.0, "within_threshold": True},
            "bad_speculation": {"diff_pct": 0.0, "within_threshold": True},
        },
        "memory": {"bandwidth_gbps": {"diff_pct": 0.0, "within_threshold": True}},
        "hotspot_coverage": {"coverage_pct": 85.0},
        "convergence": {"converged": False, "reason": ""},
    }
    from agent.adjustment import deterministic_revise

    adj = deterministic_revise(instr, report, _SENS, IterationHistory(customer_name="t"))
    assert len(adj) == 1
    assert adj[0]["knob"] == "compute_ratio"  # retiring via compute_ratio
    assert adj[0]["expected_metric"] == "retiring"
    assert adj[0]["to"] < 0.5  # retiring too high + compute_ratio "up" => decrease


# -- enum canonicalization (LLM-tolerant intake) ---------------------------


def test_apply_canonicalizes_enum_case_variant() -> None:
    # LLM emits "MatMul"; apply stores the canonical "matmul" so the codegen
    # template's `archetype == 'matmul'` branch matches.
    out = apply_adjustments(
        _instr(),
        [
            {
                "stage": "comp_stage",
                "knob": "archetype",
                "from": "matmul",
                "to": "MatMul",
                "rationale": "",
                "expected_metric": "retiring",
                "expected_direction": "up",
            }
        ],
    )
    assert out["stages"][1]["strategies"][0]["synthesis_config"]["archetype"] == "matmul"


def test_apply_canonicalizes_enum_whitespace_variant() -> None:
    out = apply_adjustments(
        _instr(),
        [
            {
                "stage": "comp_stage",
                "knob": "archetype",
                "from": "matmul",
                "to": "  hash  ",
                "rationale": "",
                "expected_metric": "retiring",
                "expected_direction": "up",
            }
        ],
    )
    assert out["stages"][1]["strategies"][0]["synthesis_config"]["archetype"] == "hash"


def test_apply_rejects_truly_unknown_enum_after_canonicalize() -> None:
    # "n_queens" matches no domain value -> canonicalize returns it unchanged
    # -> _validate_value rejects as domain violation.
    with pytest.raises(ValueError, match="invalid value"):
        apply_adjustments(
            _instr(),
            [
                {
                    "stage": "comp_stage",
                    "knob": "archetype",
                    "from": "matmul",
                    "to": "n_queens",
                    "rationale": "",
                    "expected_metric": "retiring",
                    "expected_direction": "up",
                }
            ],
        )


def test_canonicalize_leaves_numeric_knob_untouched() -> None:
    # Numeric knobs are not enums; canonicalization is a no-op.
    from agent.adjustment import _canonicalize_enum

    assert _canonicalize_enum("working_set_mb", 256) == 256
    assert _canonicalize_enum("compute_ratio", 0.8) == 0.8


def test_validate_enum_trusted_when_values_metric_values_length_mismatch() -> None:
    # values has 3 entries, metric_values has 2 -> cannot index safely -> trust.
    instr = {
        "stages": [
            {"stage_name": "m", "strategies": [{"synthesis_config": {"access_pattern": "mixed"}}]}
        ]
    }
    sens = {
        "access_pattern": {
            "target_metric": "backend_bound",
            "expected_direction": "up",
            "verdict": "controllable",
            "values": ["sequential", "mixed", "random"],
            "metric_values": [30.0, 50.0],  # length mismatch
        }
    }
    from agent.adjustment import validate_adjustments

    accepted, _ = validate_adjustments(
        [
            {
                "stage": "m",
                "knob": "access_pattern",
                "from": "mixed",
                "to": "sequential",
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        sens,
        tier="structural",
    )
    assert len(accepted) == 1


def test_validate_enum_trusted_when_values_empty() -> None:
    instr = {
        "stages": [
            {"stage_name": "m", "strategies": [{"synthesis_config": {"access_pattern": "mixed"}}]}
        ]
    }
    sens = {
        "access_pattern": {
            "target_metric": "backend_bound",
            "expected_direction": "up",
            "verdict": "controllable",
        }
    }
    from agent.adjustment import validate_adjustments

    accepted, _ = validate_adjustments(
        [
            {
                "stage": "m",
                "knob": "access_pattern",
                "from": "mixed",
                "to": "sequential",
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        sens,
        tier="structural",
    )
    assert len(accepted) == 1


def test_validate_enum_trusted_when_actual_not_in_values() -> None:
    # actual ("weird") not in spike values, to ("sequential") is -> cannot compare -> trust.
    instr = {
        "stages": [
            {"stage_name": "m", "strategies": [{"synthesis_config": {"access_pattern": "weird"}}]}
        ]
    }
    sens = {
        "access_pattern": {
            "target_metric": "backend_bound",
            "expected_direction": "up",
            "verdict": "controllable",
            "values": ["sequential", "mixed", "random"],
            "metric_values": [30.0, 50.0, 70.0],
        }
    }
    from agent.adjustment import validate_adjustments

    accepted, _ = validate_adjustments(
        [
            {
                "stage": "m",
                "knob": "access_pattern",
                "from": "weird",
                "to": "sequential",
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        sens,
        tier="structural",
    )
    assert len(accepted) == 1


def test_validate_enum_trusted_when_to_not_in_values() -> None:
    instr = {
        "stages": [
            {"stage_name": "m", "strategies": [{"synthesis_config": {"access_pattern": "mixed"}}]}
        ]
    }
    sens = {
        "access_pattern": {
            "target_metric": "backend_bound",
            "expected_direction": "up",
            "verdict": "controllable",
            "values": ["sequential", "mixed", "random"],
            "metric_values": [30.0, 50.0, 70.0],
        }
    }
    from agent.adjustment import validate_adjustments

    accepted, _ = validate_adjustments(
        [
            {
                "stage": "m",
                "knob": "access_pattern",
                "from": "mixed",
                "to": "weird",
                "rationale": "",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            }
        ],
        instr,
        _report(backend_diff=20.0),
        sens,
        tier="structural",
    )
    # 'weird' is not a valid enum -> domain_violation at the domain check, NOT trusted.
    # (Documents that the trust path is only reached for in-domain enum values.)
    assert accepted == []
