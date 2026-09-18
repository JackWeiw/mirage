"""Tests for the SynthesisOrchestrator (offline deterministic + LLM patch + skip real_call)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.llm_client import LLMClient
from agent.orchestrator import SynthesisOrchestrator
from agent.synthesis_plan import SynthesisPlan
from agent.synthesizer import SynthesizerAgent
from codegen.call_tree import CallSpec, SelfWork
from codegen.generator import WorkloadGenerator
from codegen.module_graph import FunctionSignature, ModuleDescriptor, ModuleGraph
from config.framework_config import AgentConfig

if TYPE_CHECKING:
    import pathlib


def _synthesis_graph() -> ModuleGraph:
    """Two modules, all synthesis kind -> all body_synthesis tasks (LLM patchable)."""
    ns_b = ModuleDescriptor(
        name="ns_b",
        namespace="ns_b",
        public_interface=[
            FunctionSignature(
                function="bar",
                namespace="ns_b",
                call_spec=CallSpec(includes=[], statement="bar()", setup=""),
                self_work=SelfWork(kind="synthesis", archetype="compute", units=40),
            )
        ],
    )
    ns_a = ModuleDescriptor(
        name="ns_a",
        namespace="ns_a",
        depends_on=["ns_b"],
        public_interface=[
            FunctionSignature(
                function="foo",
                namespace="ns_a",
                call_spec=CallSpec(includes=[], statement="foo()", setup=""),
                self_work=SelfWork(kind="synthesis", archetype="compute", units=40),
            )
        ],
    )
    return ModuleGraph(project_name="acme", modules=[ns_b, ns_a])


def _real_call_graph() -> ModuleGraph:
    """One module, all real_call -> all deterministic tasks (LLM skipped)."""
    store = ModuleDescriptor(
        name="store",
        namespace="store",
        public_interface=[
            FunctionSignature(
                function="put",
                namespace="store",
                call_spec=CallSpec(includes=["<folly.h>"], statement="folly::put()", setup=""),
                self_work=SelfWork(kind="real_call", archetype="compute", units=10),
            )
        ],
    )
    return ModuleGraph(project_name="acme", modules=[store])


class _StubClient(LLMClient):
    """In-memory LLM client: returns a canned body for every call."""

    def __init__(self, body: str) -> None:
        self._body = body

    def _build_sdk_client(self) -> object:
        return None

    def complete(self, prompt: str) -> tuple[str, str]:
        return self._body, "end_turn"

    def is_available(self) -> bool:
        return True

    def transient_exceptions(self) -> tuple[type[Exception], ...]:
        return ()


def _online_synthesizer(body: str) -> SynthesizerAgent:
    """Offline base, then inject a stub client so is_available() -> True."""
    syn = SynthesizerAgent(AgentConfig())
    syn._client = _StubClient(body)
    return syn


_CANNED = "// LLM-SYNTHESIZED\n"


def test_synthesize_offline_produces_deterministic_project(tmp_path: pathlib.Path) -> None:
    """Offline orchestrator -> no .cpp patched -> the generate_from_module_graph
    project stands (Phase A shape)."""
    orch = SynthesisOrchestrator(WorkloadGenerator(), SynthesizerAgent(AgentConfig()))
    plan = SynthesisPlan.from_graph(_synthesis_graph(), source="deterministic")
    out = orch.synthesize(plan, tmp_path / "project")
    assert (out / "ns_a.cpp").exists()
    assert (out / "ns_b.cpp").exists()
    assert (out / "CMakeLists.txt").exists()
    # not patched (offline): the .cpp is the deterministic body, NOT the canned body
    assert (out / "ns_a.cpp").read_text() != _CANNED


def test_synthesize_online_patches_body_synthesis_modules(tmp_path: pathlib.Path) -> None:
    """Online synthesizer -> modules with body_synthesis tasks get their .cpp
    overwritten with the LLM body; .h contracts stay deterministic."""
    orch = SynthesisOrchestrator(WorkloadGenerator(), _online_synthesizer(_CANNED))
    plan = SynthesisPlan.from_graph(_synthesis_graph(), source="llm")
    out = orch.synthesize(plan, tmp_path / "project")
    # both modules are all-synthesis -> both patched
    assert (out / "ns_a.cpp").read_text() == _CANNED
    assert (out / "ns_b.cpp").read_text() == _CANNED
    # .h contracts stay deterministic (NOT patched): still has the pragma guard
    assert "pragma once" in (out / "ns_a.h").read_text()


def test_synthesize_online_skips_real_call_modules(tmp_path: pathlib.Path) -> None:
    """A module whose tasks are all real_call (deterministic) -> no LLM call ->
    .cpp stays deterministic (not the canned body), even with an online synthesizer."""
    orch = SynthesisOrchestrator(WorkloadGenerator(), _online_synthesizer(_CANNED))
    plan = SynthesisPlan.from_graph(_real_call_graph(), source="llm")
    out = orch.synthesize(plan, tmp_path / "project")
    assert (out / "store.cpp").exists()
    # NOT patched (all real_call -> skipped) -> not the canned body
    assert (out / "store.cpp").read_text() != _CANNED
