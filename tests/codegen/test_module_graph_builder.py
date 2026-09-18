"""Tests for deterministic ModuleGraph recovery."""

import pytest

from codegen.module_graph_builder import ModuleGraphBuilder
from ingestion.classifier import FunctionClassifier
from profile.profile_schema import CallTreeNode, HotspotFunction, Profile, ProfileMetadata


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


def test_name_collision_fails_loud() -> None:
    # foo::store and bar::store both collapse to module "store" — P1 rejects
    # this rather than silently dropping one module's file.
    paths = [["main", "foo::store::put()", "bar::store::save()"]]
    with pytest.raises(ValueError, match="module name collision"):
        ModuleGraphBuilder(classifier=FunctionClassifier()).build(_profile(paths), "demo")


def _profile_with_call_tree() -> Profile:
    # main -> foo::hash::compute (self 40, archetype hash by keyword); a hash-named
    # leaf under a real namespace, so the new path must NOT hardcode "compute".
    root = CallTreeNode(
        function="main",
        library="custom",
        source="customer_custom",
        self_samples=0,
        cumulative_samples=40,
        depth=0,
        children=[
            CallTreeNode(
                function="foo::hash::compute(int)",
                library="custom",
                source="customer_custom",
                self_samples=40,
                cumulative_samples=40,
                depth=1,
            ),
        ],
    )
    return Profile(
        metadata=ProfileMetadata(customer="c", date="2026-09-18"),
        call_tree=[root],
    )


def test_build_from_call_tree_uses_real_archetype_not_hardcoded() -> None:
    """New path: a hash-named leaf gets archetype 'hash' via ArchetypeInferrer,
    not the legacy hardcoded 'compute' (spec section 8.1 un-block)."""
    graph = ModuleGraphBuilder(classifier=FunctionClassifier()).build(
        _profile_with_call_tree(), "demo"
    )
    found = next(
        (
            f
            for m in graph.modules
            for f in m.public_interface + m.internal_functions
            if f.function == "compute"
        ),
        None,
    )
    assert found is not None
    assert found.self_work.archetype == "hash"
    assert found.self_work.units == 40


def test_build_falls_back_to_legacy_when_call_tree_absent() -> None:
    """Sunset-gated fallback (spec section 8.1): a Profile with only flat hotspots
    (no call_tree) still builds via the legacy path -- hardcoded 'compute' stays
    until the sunset criteria remove it."""
    paths = [["main", "foo::hash::compute(int)"]]
    graph = ModuleGraphBuilder(classifier=FunctionClassifier()).build(_profile(paths), "demo")
    found = next(
        (
            f
            for m in graph.modules
            for f in m.public_interface + m.internal_functions
            if f.function == "compute"
        ),
        None,
    )
    assert found is not None
    # legacy path still hardcodes 'compute' (the known impoverishment, retained).
    assert found.self_work.archetype == "compute"
