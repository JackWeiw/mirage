"""Tests for AgentCore.revise_instruction (mock-agent, no real LLM)."""

from typing import Any

import pytest

from agent.agent_core import (
    PROMPTS_DIR,
    AgentCore,
    LLMResponseError,
    _serialize_recent_history,
)


def test_revise_instruction_prompt_exists_and_has_placeholders() -> None:
    text = (PROMPTS_DIR / "revise_instruction.md").read_text()
    assert "{prior_instruction}" in text
    assert "{report}" in text
    assert "{sensitivity}" in text
    assert "{recent_history}" in text
    # The prompt must state the proven-direction constraint and the output schema.
    assert "proven direction" in text.lower()
    assert "revised_instruction" in text
    assert "adjustments" in text


class _FakeRecord:
    """Duck-typed IterationRecord for _serialize_recent_history tests."""

    def __init__(
        self,
        adjustments: list[dict[str, Any]] | None = None,
        observed_effects: dict[str, float] | None = None,
        score: float | None = None,
        build_failed: bool = False,
        build_stderr: str = "",
    ) -> None:
        self.adjustments = adjustments or []
        self.applied_moves: list[dict[str, Any]] = []
        self.observed_effects = observed_effects or {}
        self.score = score
        self.build_failed = build_failed
        self.build_stderr = build_stderr


class _FakeHistory:
    def __init__(self, records: list[_FakeRecord]) -> None:
        self.records = records


def test_serialize_recent_history_surfaces_build_stderr() -> None:
    # A build-failed record must carry its compiler stderr into the revise
    # prompt so the LLM can self-correct a codegen compile error (#3b-fu1).
    history = _FakeHistory(
        records=[
            _FakeRecord(adjustments=[{"knob": "working_set_mb"}], score=0.4),
            _FakeRecord(
                adjustments=[], score=None, build_failed=True, build_stderr="undeclared id 'bar'"
            ),
        ]
    )
    serialized = _serialize_recent_history(history)
    assert "build_failed" in serialized
    assert "build_stderr" in serialized
    assert "undeclared id" in serialized


def test_serialize_recent_history_omits_build_fields_when_no_failure() -> None:
    history = _FakeHistory(records=[_FakeRecord(adjustments=[{"knob": "qps"}], score=0.5)])
    serialized = _serialize_recent_history(history)
    assert "build_failed" not in serialized
    assert "build_stderr" not in serialized


def _make_agent(recorded_response: dict[str, Any]) -> AgentCore:
    """AgentCore with _call_llm_json mocked to return a recorded response."""
    agent = AgentCore()  # api_key=None -> offline; _call_llm_json is mocked below
    agent._call_llm_json = lambda prompt: recorded_response  # type: ignore[method-assign]
    return agent


def test_revise_instruction_returns_revised_and_adjustments() -> None:
    recorded = {
        "revised_instruction": {"project_name": "sim", "stages": [], "config": {}},
        "adjustments": [
            {
                "stage": "mem_stage",
                "knob": "working_set_mb",
                "from": 64,
                "to": 32,
                "rationale": "lower backend",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            },
        ],
    }
    agent = _make_agent(recorded)
    revised, adjustments = agent.revise_instruction(
        prior_instruction={"project_name": "sim", "stages": [], "config": {}},
        report={"topdown_l1": {"backend_bound": {"diff_pct": 20.0, "within_threshold": False}}},
        sensitivity={
            "working_set_mb": {
                "target_metric": "backend_bound",
                "expected_direction": "up",
            }
        },
        history=None,
    )
    assert revised == recorded["revised_instruction"]
    assert adjustments == recorded["adjustments"]


def test_revise_instruction_raises_on_missing_revised_instruction() -> None:
    recorded: dict[str, Any] = {"adjustments": []}  # no revised_instruction
    agent = _make_agent(recorded)
    with pytest.raises(LLMResponseError):
        agent.revise_instruction(
            prior_instruction={},
            report={},
            sensitivity={},
            history=None,
        )


def test_revise_instruction_raises_on_non_dict_adjustment() -> None:
    recorded = {"revised_instruction": {}, "adjustments": [{"knob": "x"}, "not a dict"]}
    agent = _make_agent(recorded)
    with pytest.raises(LLMResponseError):
        agent.revise_instruction(
            prior_instruction={},
            report={},
            sensitivity={},
            history=None,
        )


def test_revise_instruction_gate_catches_hallucination_and_wrong_tier() -> None:
    # The LLM (mocked) emits 3 adjustments on a STRUCTURAL tier:
    #   - good: working_set_mb 64->32 (decreases backend, which is too high)
    #   - wrong-direction: working_set_mb 64->128 (increases backend further)
    #   - wrong-tier: memory_ratio (a RUNTIME knob) emitted on a structural tier
    # The loop runs validate_adjustments(tier="structural") on the result.
    from agent.adjustment import validate_adjustments

    instr = {
        "stages": [
            {
                "stage_name": "mem_stage",
                "strategies": [
                    {"synthesis_config": {"working_set_mb": 64, "access_pattern": "random"}}
                ],
            }
        ],
        "config": {"memory_ratio": 0.5},
    }
    report = {
        "topdown_l1": {
            "backend_bound": {"diff_pct": 20.0, "within_threshold": False},
            "retiring": {"diff_pct": 0.0, "within_threshold": True},
            "frontend_bound": {"diff_pct": 0.0, "within_threshold": True},
            "bad_speculation": {"diff_pct": 0.0, "within_threshold": True},
        },
    }
    sensitivity = {
        "working_set_mb": {
            "target_metric": "backend_bound",
            "expected_direction": "up",
            "verdict": "controllable",
        },
        "memory_ratio": {
            "target_metric": "backend_bound",
            "expected_direction": "up",
            "verdict": "controllable",
        },
    }
    recorded = {
        "revised_instruction": instr,
        "adjustments": [
            {
                "stage": "mem_stage",
                "knob": "working_set_mb",
                "from": 64,
                "to": 32,
                "rationale": "lower backend",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            },
            {
                "stage": "mem_stage",
                "knob": "working_set_mb",
                "from": 64,
                "to": 128,
                "rationale": "raise backend (hallucinated)",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            },
            {
                "stage": "",
                "knob": "memory_ratio",
                "from": 0.5,
                "to": 0.2,
                "rationale": "runtime knob on structural tier",
                "expected_metric": "backend_bound",
                "expected_direction": "up",
            },
        ],
    }
    agent = _make_agent(recorded)
    _revised, adjustments = agent.revise_instruction(instr, report, sensitivity, history=None)

    accepted, rejected = validate_adjustments(
        adjustments, instr, report, sensitivity, tier="structural"
    )
    assert len(accepted) == 1
    assert accepted[0]["knob"] == "working_set_mb"
    assert accepted[0]["to"] == 32
    reasons = sorted(r["reason"] for r in rejected)
    assert reasons == ["runtime_knob_not_owned_on_structural_tier", "wrong_direction"]
