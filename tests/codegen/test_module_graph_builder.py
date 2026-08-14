"""Tests for deterministic ModuleGraph recovery."""

import pytest

from codegen.module_graph_builder import ModuleGraphBuilder
from ingestion.classifier import FunctionClassifier
from profile.profile_schema import HotspotFunction, Profile, ProfileMetadata


def _profile(call_paths: list[list[str]]) -> Profile:
    hotspots = [
        HotspotFunction(
            function=path[-1],
            library="custom",
            source="customer_custom",
            self_pct=10.0,
            cumulative_pct=100.0,
            call_path=path,
        )
        for path in call_paths
    ]
    return Profile(
        metadata=ProfileMetadata(customer="c", date="2026-08-14"),
        hotspots=hotspots,
    )


def test_clusters_by_namespace_and_marks_public() -> None:
    # foo::index::lookup calls foo::store::put; index depends on store, put is public.
    paths = [
        ["main", "foo::index::lookup(int)", "foo::store::put(int)"],
        ["main", "foo::index::lookup(int)", "foo::index::helper()"],
    ]
    graph = ModuleGraphBuilder(classifier=FunctionClassifier()).build(_profile(paths), "demo")
    names = {m.name for m in graph.modules}
    assert "index" in names
    assert "store" in names
    store = next(m for m in graph.modules if m.name == "store")
    assert any(f.function == "put" for f in store.public_interface)
    index = next(m for m in graph.modules if m.name == "index")
    assert "store" in index.depends_on


def test_internal_function_not_public() -> None:
    # helper is only called within foo::index -> internal.
    paths = [["main", "foo::index::lookup(int)", "foo::index::helper()"]]
    graph = ModuleGraphBuilder(classifier=FunctionClassifier()).build(_profile(paths), "demo")
    index = next(m for m in graph.modules if m.name == "index")
    assert any(f.function == "helper" for f in index.internal_functions)
    assert not any(f.function == "helper" for f in index.public_interface)


def test_cycle_fails_loud() -> None:
    # a::f -> b::g -> a::h across namespaces forms a->b->a cycle.
    paths = [["a::f()", "b::g()", "a::h()"]]
    with pytest.raises(ValueError, match="cycle"):
        ModuleGraphBuilder(classifier=FunctionClassifier()).build(_profile(paths), "demo")
