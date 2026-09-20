"""Tests for the ArchitectAgent (deterministic + LLM refine + fallback)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.agent_core import LLMResponseError
from agent.architect import ArchitectAgent
from agent.llm_client import LLMClient
from agent.synthesis_plan import SynthesisPlan
from codegen.module_graph_builder import ModuleGraphBuilder
from config.framework_config import AgentConfig
from profile.profile_schema import CallTreeNode, Profile, ProfileMetadata


def _profile_with_call_tree() -> Profile:
    root = CallTreeNode(
        function="main",
        library="custom",
        source="customer_custom",
        self_samples=0,
        cumulative_samples=100,
        depth=0,
        children=[
            CallTreeNode(
                function="ns_a::foo()",
                library="custom",
                source="customer_custom",
                self_samples=40,
                cumulative_samples=80,
                depth=1,
                children=[
                    CallTreeNode(
                        function="ns_b::bar()",
                        library="custom",
                        source="customer_custom",
                        self_samples=40,
                        cumulative_samples=40,
                        depth=2,
                    ),
                ],
            ),
        ],
    )
    return Profile(
        metadata=ProfileMetadata(customer="acme", date="2026-09-19"),
        call_tree=[root],
    )


class _StubClient(LLMClient):
    """In-memory LLM client: returns a canned JSON plan, ignores the prompt."""

    def __init__(self, plan_json: str) -> None:
        self._plan_json = plan_json

    def _build_sdk_client(self) -> object:
        return None

    def complete(self, prompt: str) -> tuple[str, str]:
        return self._plan_json, "end_turn"

    def is_available(self) -> bool:
        return True

    def transient_exceptions(self) -> tuple[type[Exception], ...]:
        return ()


def _offline_architect() -> ArchitectAgent:
    return ArchitectAgent(AgentConfig())  # api_key=None -> offline


def _stub_architect(plan_json: str) -> ArchitectAgent:
    """Offline base, then inject the stub client so is_available() -> True."""
    arch = ArchitectAgent(AgentConfig())
    arch._client = _StubClient(plan_json)
    return arch


def test_design_plan_deterministic_when_no_llm() -> None:
    """Agent-optional: no api_key -> architect returns the deterministic base
    enveloped as a SynthesisPlan, source='deterministic'."""
    arch = _offline_architect()
    plan = arch.design_plan(_profile_with_call_tree())
    assert plan.source == "deterministic"
    mod_names = {m.name for m in plan.module_graph.modules}
    assert {"main", "ns_a", "ns_b"}.issubset(mod_names)
    assert len(plan.tasks) >= 2
    assert all(t.status == "pending" for t in plan.tasks)


def test_design_plan_llm_refines_and_source_is_llm() -> None:
    """With an LLM, the architect emits source='llm'. The stub returns the
    deterministic base re-serialized as a plan (refinement content is an LLM
    concern; here we assert the architect parses + tags source correctly)."""
    profile = _profile_with_call_tree()
    from agent.synthesis_plan import SynthesisPlan
    from codegen.module_graph_builder import ModuleGraphBuilder

    base = ModuleGraphBuilder().build(profile, project_name="acme")
    base_plan = SynthesisPlan.from_graph(base, source="llm")
    arch = _stub_architect(base_plan.model_dump_json())
    plan = arch.design_plan(profile)
    assert plan.source == "llm"
    assert len(plan.module_graph.modules) == len(base.modules)


def test_design_plan_falls_back_on_invalid_llm_response() -> None:
    """If the LLM returns unparseable JSON, the architect falls back to the
    deterministic base (agent-optional resilience, no crash)."""
    arch = _stub_architect("this is not json { incomplete")
    plan = arch.design_plan(_profile_with_call_tree())
    assert plan.source == "deterministic"
    assert len(plan.module_graph.modules) >= 2


def _seeded_plan() -> SynthesisPlan:
    """A plan with two synthesized bodies cached (for revise_plan tests)."""
    plan = SynthesisPlan.from_graph(
        ModuleGraphBuilder().build(_profile_with_call_tree(), project_name="acme"),
        source="llm",
    )
    plan.synthesized_bodies = {"ns_a": "// body a", "ns_b": "// body b"}
    return plan


def test_revise_plan_clears_targeted_module_bodies() -> None:
    """revise_plan: LLM names modules to re-synthesize; we clear their cached
    synthesized_bodies (cache-miss -> orchestrator re-synthesizes only them),
    preserving unchanged modules' bodies. LLM-only (loop calls when available)."""
    arch = _stub_architect(
        json.dumps(
            {
                "resynthesize_modules": ["ns_a"],
                "adjustments": [{"module": "ns_a", "reason": "backend_bound gap"}],
            }
        )
    )
    revised, adjustments = arch.revise_plan(
        _seeded_plan(), {"topdown_l1": {}}, {}, SimpleNamespace(records=[])
    )
    assert "ns_a" not in revised.synthesized_bodies
    assert revised.synthesized_bodies["ns_b"] == "// body b"
    assert adjustments == [{"module": "ns_a", "reason": "backend_bound gap"}]


def test_revise_plan_malformed_response_raises() -> None:
    """A malformed LLM response (no resynthesize_modules) raises LLMResponseError
    -> the loop catches LLMError and degrades to the runtime tier."""
    arch = _stub_architect("{}")  # missing resynthesize_modules + adjustments
    with pytest.raises(LLMResponseError):
        arch.revise_plan(_seeded_plan(), {"topdown_l1": {}}, {}, SimpleNamespace(records=[]))


def test_revise_plan_empty_resynth_keeps_all_bodies() -> None:
    """If the LLM returns an empty resynthesize_modules (no module clearly drives
    the gap), the plan is returned unchanged (all bodies preserved)."""
    arch = _stub_architect(json.dumps({"resynthesize_modules": [], "adjustments": []}))
    revised, adjustments = arch.revise_plan(
        _seeded_plan(), {"topdown_l1": {}}, {}, SimpleNamespace(records=[])
    )
    assert revised.synthesized_bodies == {"ns_a": "// body a", "ns_b": "// body b"}
    assert adjustments == []
