"""Tests for the ModuleGraph IR."""

from codegen.call_tree import CallSpec, SelfWork
from codegen.module_graph import FunctionSignature, ModuleDescriptor, ModuleGraph


def _sig(function: str, declaration: str | None = None) -> FunctionSignature:
    return FunctionSignature(
        function=function,
        namespace="foo::index",
        call_spec=CallSpec(includes=["foo/index.h"], statement=f"{function}()", setup=""),
        declaration=declaration,
        self_work=SelfWork(kind="synthesis", archetype="compute", units=100),
    )


def test_module_graph_round_trip() -> None:
    pub = _sig("lookup", "void lookup(int)")
    internal = _sig("helper")
    mod = ModuleDescriptor(
        name="index",
        namespace="foo::index",
        public_interface=[pub],
        internal_functions=[internal],
        depends_on=["store"],
    )
    graph = ModuleGraph(project_name="demo", modules=[mod])
    assert graph.modules[0].public_interface[0].declaration == "void lookup(int)"
    assert graph.modules[0].depends_on == ["store"]
    assert graph.modules[0].internal_functions[0].function == "helper"
    assert graph.modules[0].public_interface[0].thread_pool is None


def test_module_graph_empty_is_valid() -> None:
    g = ModuleGraph(project_name="empty")
    assert g.modules == []
