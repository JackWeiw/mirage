"""Tests for AgentCore."""

import json
import pathlib
from typing import Any

import pytest

from agent.agent_core import AgentCore
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
    response = "No JSON here, just plain text."
    result = agent._parse_json_response(response)
    assert "raw_response" in result


def test_agent_core_parse_json_response_malformed_json() -> None:
    config = AgentConfig(api_key=None)
    agent = AgentCore(config=config)
    response = '{"key": invalid}'
    result = agent._parse_json_response(response)
    assert "raw_response" in result


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
