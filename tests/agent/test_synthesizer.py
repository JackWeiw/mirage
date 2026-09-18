"""Tests for the SynthesizerAgent (offline skip + LLM body + LLM failure)."""

from __future__ import annotations

from agent.agent_core import LLMError
from agent.llm_client import LLMClient
from agent.synthesizer import SynthesizerAgent
from codegen.call_tree import CallSpec, SelfWork
from codegen.module_graph import FunctionSignature
from config.framework_config import AgentConfig


def _sigs() -> list[FunctionSignature]:
    return [
        FunctionSignature(
            function="foo",
            namespace="ns_a",
            call_spec=CallSpec(includes=[], statement="foo()", setup=""),
            self_work=SelfWork(kind="synthesis", archetype="compute", units=40),
        )
    ]


class _StubClient(LLMClient):
    """In-memory LLM client: returns a canned body (or raises LLMError)."""

    def __init__(self, body: str, *, fail: bool = False) -> None:
        self._body = body
        self._fail = fail

    def _build_sdk_client(self) -> object:
        return None

    def complete(self, prompt: str) -> tuple[str, str]:
        if self._fail:
            raise LLMError("stub failure")
        return self._body, "end_turn"

    def is_available(self) -> bool:
        return True

    def transient_exceptions(self) -> tuple[type[Exception], ...]:
        return ()


def _offline() -> SynthesizerAgent:
    return SynthesizerAgent(AgentConfig())  # api_key=None -> offline


def _stub(body: str, *, fail: bool = False) -> SynthesizerAgent:
    syn = SynthesizerAgent(AgentConfig())
    syn._client = _StubClient(body, fail=fail)
    return syn


def test_synthesize_offline_returns_none() -> None:
    """Agent-optional: no api_key -> skip synthesis, return None (orchestrator
    falls back to the deterministic body generate_from_module_graph emits)."""
    assert _offline().synthesize("ns_a", "ns_a", _sigs(), {}) is None


def test_synthesize_llm_returns_body() -> None:
    """With an LLM, synthesize returns the raw C++ body the LLM produced."""
    body = '#include "ns_a.h"\nnamespace ns_a { void foo() {} }\n'
    result = _stub(body).synthesize("ns_a", "ns_a", _sigs(), {"self_pct": 40.0})
    assert result == body


def test_synthesize_llm_failure_returns_none() -> None:
    """If the LLM call raises, synthesize returns None (no crash, no retry delay
    -- the stub raises a non-transient LLMError which _call_llm propagates at once)."""
    assert _stub("unused", fail=True).synthesize("ns_a", "ns_a", _sigs(), {}) is None
