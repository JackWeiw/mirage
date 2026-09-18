"""Tests for the SynthesisPlan IR (architect output, codegen input)."""

from __future__ import annotations

from agent.synthesis_plan import SynthesisPlan, SynthesisTask
from codegen.call_tree import CallSpec, SelfWork
from codegen.module_graph import FunctionSignature, ModuleDescriptor, ModuleGraph


def _graph() -> ModuleGraph:
    """Two modules: ns_a (public foo + internal helper, depends on ns_b) + ns_b (public bar)."""
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
        internal_functions=[
            FunctionSignature(
                function="helper",
                namespace="ns_a",
                call_spec=CallSpec(includes=[], statement="helper()", setup=""),
                self_work=SelfWork(kind="synthesis", archetype="compute", units=5),
            )
        ],
    )
    return ModuleGraph(project_name="acme", modules=[ns_b, ns_a])


def test_synthesis_task_from_signature_classifies_role() -> None:
    sig = FunctionSignature(
        function="put",
        namespace="store",
        call_spec=CallSpec(includes=["<folly.h>"], statement="folly::put()", setup=""),
        self_work=SelfWork(kind="real_call", archetype="compute", units=10),
    )
    task = SynthesisTask.from_signature(sig, module="store")
    assert task.module == "store"
    assert task.signature.function == "put"
    # real_call (open-source direct) -> deterministic (skips LLM in Phase C)
    assert task.role == "deterministic"
    assert task.status == "pending"
    assert task.result is None


def test_synthesis_task_from_signature_synthesis_is_body_synthesis() -> None:
    sig = FunctionSignature(
        function="foo",
        namespace="ns_a",
        call_spec=CallSpec(includes=[], statement="foo()", setup=""),
        self_work=SelfWork(kind="synthesis", archetype="memory", units=40),
    )
    task = SynthesisTask.from_signature(sig, module="ns_a")
    assert task.role == "body_synthesis"


def test_synthesis_plan_from_graph_envelopes_every_function() -> None:
    graph = _graph()
    plan = SynthesisPlan.from_graph(graph, source="deterministic")
    assert plan.source == "deterministic"
    assert plan.module_graph is graph
    # one task per public+internal function across all modules
    names = sorted(t.signature.function for t in plan.tasks)
    assert names == ["bar", "foo", "helper"]
    # each task's module matches its signature's module
    for t in plan.tasks:
        assert t.module in {"ns_a", "ns_b"}


def test_synthesis_plan_round_trips_json() -> None:
    plan = SynthesisPlan.from_graph(_graph(), source="deterministic")
    loaded = SynthesisPlan.model_validate_json(plan.model_dump_json())
    assert loaded.source == "deterministic"
    assert len(loaded.tasks) == 3
    assert loaded.module_graph.modules[0].name in {"ns_a", "ns_b"}
    # task envelope survives the round-trip (signature reused, not duplicated)
    assert loaded.tasks[0].signature.function in {"bar", "foo", "helper"}
