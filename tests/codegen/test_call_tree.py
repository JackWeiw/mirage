"""Tests for CallTreeBuilder and the SkeletonDescriptor IR."""

import pathlib

from codegen.call_tree import CallTreeBuilder

EXAMPLE_FG = (
    pathlib.Path(__file__).parent.parent.parent
    / "examples"
    / "search_ranking"
    / "customer_data"
    / "flamegraph_folded.txt"
)


def test_build_merges_and_classifies() -> None:
    stacks = [
        (["main", "Svc::process", "StageA::run", "folly::X"], 100),
        (["main", "Svc::process", "StageA::run", "Customer::hashFeature"], 50),
        (["main", "Svc::process", "StageB::run", "Customer::sortMerge"], 30),
    ]
    desc = CallTreeBuilder().build(stacks, profile=None, project_name="t")
    assert desc.trunk == ["main", "Svc::process"]
    assert desc.service_node == "Svc::process"
    # process is the trunk-end; its children are the stages
    service = desc.root.children[0]  # process
    stage_a, stage_b = service.children
    funcs = {c.function for c in stage_a.children}
    assert "folly::X" in funcs  # open-source leaf kept as real call
    synth = [c for c in stage_a.children if c.node_kind == "custom_synth"]
    assert len(synth) == 1  # custom leaves merged into one synth
    assert abs(synth[0].self_pct - (50 / 180 * 100)) < 0.01
    assert synth[0].self_work.archetype == "hash"
    synth_b = [c for c in stage_b.children if c.node_kind == "custom_synth"]
    assert len(synth_b) == 1 and synth_b[0].self_work.archetype == "sort"


def test_self_budget_on_interior_nodes() -> None:
    # process has its own self-time (leaf of the first line).
    stacks = [
        (["main", "Svc::process"], 20),
        (["main", "Svc::process", "folly::X"], 80),
    ]
    desc = CallTreeBuilder().build(stacks, profile=None, project_name="t")
    process = desc.root.children[0]
    assert process.node_kind in ("trunk", "stage")
    assert abs(process.self_pct - 20.0) < 0.01
    assert process.self_work.kind == "self_budget"


def test_build_from_example_flamegraph() -> None:
    from ingestion.flamegraph_parser import FlamegraphParser

    stacks = FlamegraphParser().parse_stacks(EXAMPLE_FG)
    assert stacks  # non-empty
    desc = CallTreeBuilder().build(stacks, profile=None, project_name="search_ranking_sim")
    # trunk includes main and the service process frame
    assert desc.trunk[0] == "main"
    assert len(desc.trunk) >= 2
    # at least one open-source leaf and one custom synth across the tree
    open_leaves = _collect(desc.root, "open_source_leaf")
    synths = _collect(desc.root, "custom_synth")
    assert open_leaves, "expected open-source real-call leaves"
    assert synths, "expected collapsed custom synth nodes"


def _collect(node: object, kind: str) -> list[object]:
    out: list[object] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if getattr(n, "node_kind", None) == kind:
            out.append(n)
        stack.extend(getattr(n, "children", []))
    return out
