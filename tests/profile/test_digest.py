"""Tests for the signature-digest accessors (thin derivations, not a stored IR)."""

from profile.digest import (
    archetype_of,
    dominant_bottleneck,
    hotspot_subtree,
    per_module_self_pct,
    stages,
)
from profile.profile_schema import (
    BusinessModel,
    CallTreeNode,
    Profile,
    ProfileMetadata,
    Stage,
    TopdownNode,
)


def _profile_with_tree() -> Profile:
    # main -> a (self 40) ; main -> b (self 10)
    root = CallTreeNode(
        function="main",
        source="customer_custom",
        self_samples=0,
        cumulative_samples=50,
        self_pct=0.0,
        cumulative_pct=100.0,
        depth=0,
        children=[
            CallTreeNode(
                function="folly::hash",
                library="folly",
                source="open_source",
                self_samples=40,
                cumulative_samples=40,
                self_pct=80.0,
                cumulative_pct=80.0,
                depth=1,
            ),
            CallTreeNode(
                function="b",
                library="custom",
                source="customer_custom",
                self_samples=10,
                cumulative_samples=10,
                self_pct=20.0,
                cumulative_pct=20.0,
                depth=1,
            ),
        ],
    )
    return Profile(
        metadata=ProfileMetadata(customer="acme", date="2026-09-18"),
        call_tree=[root],
    )


def test_hotspot_subtree_top_k() -> None:
    profile = _profile_with_tree()
    top = hotspot_subtree(profile, top_k=1)
    assert len(top) == 1
    assert top[0].function == "folly::hash"  # self 40 > b's self 10


def test_hotspot_subtree_no_tree() -> None:
    profile = Profile(metadata=ProfileMetadata(customer="x", date="d"))
    assert hotspot_subtree(profile, top_k=5) == []


def test_dominant_bottleneck_deepest_max() -> None:
    tree = [
        TopdownNode(
            name="Backend Bound",
            value=72.0,
            children=[
                TopdownNode(
                    name="Memory Bound",
                    value=38.0,
                    children=[TopdownNode(name="L3 Bound", value=30.0)],
                ),
            ],
        )
    ]
    profile = Profile(metadata=ProfileMetadata(customer="x", date="d"), topdown_tree=tree)
    assert dominant_bottleneck(profile) == "backend bound.memory bound.l3 bound"


def test_dominant_bottleneck_none_when_absent() -> None:
    profile = Profile(metadata=ProfileMetadata(customer="x", date="d"))
    assert dominant_bottleneck(profile) is None


def test_per_module_self_pct_aggregates_by_library() -> None:
    agg = per_module_self_pct(_profile_with_tree())
    # folly node self 40/50=80%, b node self 10/50=20%
    assert abs(agg["folly"] - 80.0) < 0.01
    assert abs(agg["custom"] - 20.0) < 0.01


def test_stages_returns_business_model_stages() -> None:
    profile = Profile(
        metadata=ProfileMetadata(customer="x", date="d"),
        business_model=BusinessModel(
            archetype="memory_bound",
            stages=[Stage(name="s1", bottleneck="b", dominant_self_pct=1.0)],
        ),
    )
    assert [s.name for s in stages(profile)] == ["s1"]


def test_stages_empty_when_no_business_model() -> None:
    profile = Profile(metadata=ProfileMetadata(customer="x", date="d"))
    assert stages(profile) == []


class _FakeInferrer:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def infer(self, name: str, memory_bound_dominant: bool = False) -> str:
        return self._mapping.get(name, "compute")


def test_archetype_of_delegates_to_injected_inferrer() -> None:
    node = CallTreeNode(function="folly::hash", source="open_source")
    inferrer = _FakeInferrer({"folly::hash": "hash"})
    assert archetype_of(node, inferrer) == "hash"


def test_archetype_of_defaults_compute_without_infer() -> None:
    node = CallTreeNode(function="x", source="customer_custom")
    assert archetype_of(node, object()) == "compute"
