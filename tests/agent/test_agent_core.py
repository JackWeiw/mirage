"""Tests for AgentCore."""

import json
import pathlib
import time
from types import SimpleNamespace
from typing import Any

import pytest

from agent.agent_core import (
    AgentCore,
    LLMResponseError,
    LLMTransientError,
    LLMTruncationError,
)
from config.framework_config import AgentConfig


def test_agent_core_prompt_loading() -> None:
    """Test that prompt templates can be loaded."""
    prompts_dir = pathlib.Path(__file__).parent.parent.parent / "src" / "agent" / "prompts"
    analyze = (prompts_dir / "analyze_profile.md").read_text()
    assert "customer Profile" in analyze
    assert "{profile_json}" in analyze
    assert "classify it as either" not in analyze  # LLM no longer re-classifies

    plan = (prompts_dir / "plan_workflow.md").read_text()
    assert "stage_name" in plan

    detail = (prompts_dir / "detail_fill.md").read_text()
    assert "project_name" in detail

    evaluate = (prompts_dir / "evaluate_comparison.md").read_text()
    assert "iteration_priority" in evaluate


def test_agent_core_not_available_without_key() -> None:
    config = AgentConfig(api_key=None)
    agent = AgentCore(config=config)
    assert agent.is_available() is False


def test_agent_core_raises_on_llm_call_without_client() -> None:
    config = AgentConfig(api_key=None)
    agent = AgentCore(config=config)
    with pytest.raises(RuntimeError, match="Agent not available"):
        agent._call_llm("test prompt")


def test_agent_core_parse_json_response_valid() -> None:
    config = AgentConfig(api_key=None)
    agent = AgentCore(config=config)
    response = (
        'Here is my analysis:\n{"bottleneck_type": "backend_bound",'
        ' "bottleneck_subtype": "memory_bound"}\nEnd.'
    )
    result = agent._parse_json_response(response)
    assert result["bottleneck_type"] == "backend_bound"


def test_agent_core_parse_json_response_no_json() -> None:
    config = AgentConfig(api_key=None)
    agent = AgentCore(config=config)
    with pytest.raises(LLMResponseError):
        agent._parse_json_response("No JSON here, just plain text.")


def test_agent_core_parse_json_response_malformed_json() -> None:
    config = AgentConfig(api_key=None)
    agent = AgentCore(config=config)
    with pytest.raises(LLMResponseError):
        agent._parse_json_response('{"key": invalid}')


def test_classification_from_profile_extracts_source_of_truth() -> None:
    """The classifier's source/library are pulled verbatim from the Profile JSON."""
    profile_json = json.dumps(
        {
            "hotspots": [
                {
                    "function": "folly::futures::detail::FutureImpl::then",
                    "source": "open_source",
                    "library": "folly",
                },
                {
                    "function": "CustomerCustom::hashFeature",
                    "source": "customer_custom",
                    "library": "custom",
                },
            ]
        }
    )
    assert AgentCore._classification_from_profile(profile_json) == [
        {
            "function": "folly::futures::detail::FutureImpl::then",
            "source": "open_source",
            "library": "folly",
        },
        {
            "function": "CustomerCustom::hashFeature",
            "source": "customer_custom",
            "library": "custom",
        },
    ]


def test_classification_from_profile_tolerates_malformed() -> None:
    """Bad JSON or missing fields never raise; they yield an empty classification."""
    assert AgentCore._classification_from_profile("not json") == []
    assert AgentCore._classification_from_profile("{}") == []
    assert AgentCore._classification_from_profile('{"hotspots": [{"function": "x"}]}') == []


def test_analyze_profile_injects_deterministic_classification(monkeypatch: Any) -> None:
    """The LLM response is merged with the deterministic classification injected."""
    config = AgentConfig(api_key=None)
    agent = AgentCore(config=config)
    # Stub the LLM (no API key); the response omits hotspot_classification.
    monkeypatch.setattr(agent, "_call_llm", lambda prompt: '{"bottleneck_type": "backend_bound"}')
    profile_json = json.dumps(
        {
            "hotspots": [
                {
                    "function": "folly::futures::detail::FutureImpl::then",
                    "source": "open_source",
                    "library": "folly",
                }
            ]
        }
    )
    analysis = agent.analyze_profile(profile_json)
    assert analysis["bottleneck_type"] == "backend_bound"
    assert analysis["hotspot_classification"] == [
        {
            "function": "folly::futures::detail::FutureImpl::then",
            "source": "open_source",
            "library": "folly",
        }
    ]


# -- LLM call hardening: retry, truncation, loud parse failure -----------------


class _TransientError(Exception):
    """Stand-in for a transient SDK exception in retry tests."""


def _stub_client(agent: AgentCore, create_fn: Any) -> None:
    """Replace the agent's messages.create with a stub (no real network)."""
    agent._client.messages.create = create_fn


def test_call_llm_retries_transient_then_succeeds(monkeypatch: Any) -> None:
    """Transient errors are retried with backoff; success returns the text."""
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    attempts: list[int] = []

    def fake_create(**_kwargs: Any) -> Any:
        attempts.append(1)
        if len(attempts) < 3:
            raise _TransientError("transient")
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(text='{"ok": 1}')])

    agent = AgentCore(config=AgentConfig(api_key="stub-key"))
    _stub_client(agent, fake_create)
    monkeypatch.setattr(agent, "_transient_exceptions", lambda: (_TransientError,))
    assert agent._call_llm("p") == '{"ok": 1}'
    assert len(attempts) == 3


def test_call_llm_raises_transient_after_retries_exhausted(monkeypatch: Any) -> None:
    """Always-transient errors exhaust retries and raise LLMTransientError."""
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    def always_fail(**_kwargs: Any) -> Any:
        raise _TransientError("always")

    agent = AgentCore(config=AgentConfig(api_key="stub-key"))
    _stub_client(agent, always_fail)
    monkeypatch.setattr(agent, "_transient_exceptions", lambda: (_TransientError,))
    with pytest.raises(LLMTransientError):
        agent._call_llm("p")


def test_call_llm_raises_truncation_on_max_tokens(monkeypatch: Any) -> None:
    """A max_tokens stop_reason raises LLMTruncationError (not retried)."""
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    agent = AgentCore(config=AgentConfig(api_key="stub-key"))
    _stub_client(agent, lambda **_kwargs: SimpleNamespace(stop_reason="max_tokens", content=[]))
    monkeypatch.setattr(agent, "_transient_exceptions", lambda: ())
    with pytest.raises(LLMTruncationError):
        agent._call_llm("p")


def test_call_llm_does_not_retry_non_transient(monkeypatch: Any) -> None:
    """Non-transient errors propagate immediately (no retry, no sleep)."""
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

    class _FatalError(Exception):
        pass

    def fail(**_kwargs: Any) -> Any:
        raise _FatalError("bad request")

    agent = AgentCore(config=AgentConfig(api_key="stub-key"))
    _stub_client(agent, fail)
    monkeypatch.setattr(agent, "_transient_exceptions", lambda: ())  # nothing transient
    with pytest.raises(_FatalError):
        agent._call_llm("p")
    assert sleeps == []


def test_call_llm_json_raises_on_non_json(monkeypatch: Any) -> None:
    """A non-JSON LLM response raises LLMResponseError, no silent fallback."""
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    agent = AgentCore(config=AgentConfig(api_key="stub-key"))
    _stub_client(
        agent,
        lambda **_kwargs: SimpleNamespace(
            stop_reason="end_turn", content=[SimpleNamespace(text="not json")]
        ),
    )
    monkeypatch.setattr(agent, "_transient_exceptions", lambda: ())
    with pytest.raises(LLMResponseError):
        agent._call_llm_json("p")
