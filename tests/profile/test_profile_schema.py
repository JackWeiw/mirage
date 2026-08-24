"""Tests for Profile schema validation."""

from profile.profile_schema import (
    HotspotFunction,
    MemoryProfile,
    Profile,
    ProfileMetadata,
    SoftwareDependency,
    TopdownL1,
    TopdownL2,
    TopdownL2Backend,
    TopdownL2Frontend,
    TopdownNode,
    TopdownSummary,
)


def test_profile_metadata_defaults() -> None:
    meta = ProfileMetadata(customer="test_customer", date="2026-07-27")
    assert meta.platform == "arm64"
    assert meta.kernel_version is None
    assert meta.software_stack == []


def test_profile_metadata_with_stack() -> None:
    meta = ProfileMetadata(
        customer="acme",
        date="2026-07-27",
        neoverse_core="N2",
        software_stack=[
            SoftwareDependency(name="folly", version="2.1.0", compile_flags="-O2"),
        ],
    )
    assert meta.neoverse_core == "N2"
    assert meta.software_stack[0].name == "folly"


def test_hotspot_function_open_source() -> None:
    hs = HotspotFunction(
        function="folly::futures::detail::FutureImpl::then",
        library="folly",
        source="open_source",
        self_pct=12.5,
        cumulative_pct=35.2,
        call_path=["main", "Server::handleRequest", "folly::futures::detail::FutureImpl::then"],
    )
    assert hs.source == "open_source"


def test_topdown_l1_sums_approximately_to_one_hundred() -> None:
    # TopdownL1 fields are percentages (0-100) summing to ~100 (#46).
    td = TopdownL1(frontend_bound=25.0, backend_bound=40.0, bad_speculation=10.0, retiring=25.0)
    total = td.frontend_bound + td.backend_bound + td.bad_speculation + td.retiring
    assert abs(total - 100.0) < 0.01


def test_topdown_l2_nested() -> None:
    td_l2 = TopdownL2(
        frontend_bound=TopdownL2Frontend(fetch_latency=15.0, branch_detect=5.0),
        backend_bound=TopdownL2Backend(memory_bound=30.0, core_bound=10.0),
    )
    assert td_l2.frontend_bound is not None
    assert td_l2.frontend_bound.fetch_latency == 15.0


def test_full_profile_serialization() -> None:
    profile = Profile(
        metadata=ProfileMetadata(customer="acme", date="2026-07-27", neoverse_core="N2"),
        hotspots=[
            HotspotFunction(
                function="folly::futures::detail::FutureImpl::then",
                library="folly",
                source="open_source",
                self_pct=12.5,
                cumulative_pct=35.2,
                call_path=[
                    "main",
                    "Server::handleRequest",
                    "folly::futures::detail::FutureImpl::then",
                ],
            )
        ],
        topdown=TopdownL1(
            frontend_bound=25.0, backend_bound=40.0, bad_speculation=10.0, retiring=25.0
        ),
        memory=MemoryProfile(bandwidth_gbps=45.2, l3_miss_rate=0.08),
        business_logic="High-concurrency RPC service",
    )
    json_str = profile.model_dump_json()
    loaded = Profile.model_validate_json(json_str)
    assert loaded.metadata.customer == "acme"
    assert loaded.hotspots[0].self_pct == 12.5
    assert loaded.memory is not None
    assert loaded.memory.bandwidth_gbps == 45.2


def test_topdown_node_recursive_children() -> None:
    leaf = TopdownNode(name="L3 Bound", value=30.96)
    parent = TopdownNode(name="Memory Bound", value=38.89, children=[leaf])
    assert parent.children[0].name == "L3 Bound"
    assert parent.children[0].value == 30.96
    assert parent.children[0].sampling_event is None


def test_topdown_node_sampling_event() -> None:
    node = TopdownNode(name="Retiring", value=7.38, sampling_event="inst_retired")
    assert node.sampling_event == "inst_retired"


def test_topdown_node_defaults() -> None:
    node = TopdownNode(name="Backend Bound", value=72.01)
    assert node.children == []
    assert node.sampling_event is None


def test_topdown_summary_fields() -> None:
    s = TopdownSummary(cycles=380549550617, instructions=168580377238, ipc=0.44)
    assert s.cycles == 380549550617
    assert s.instructions == 168580377238
    assert s.ipc == 0.44


def test_profile_summary_and_tree_default_none() -> None:
    profile = Profile(metadata=ProfileMetadata(customer="acme", date="2026-08-21"))
    assert profile.summary is None
    assert profile.topdown_tree is None


def test_topdown_node_round_trips_json() -> None:
    root = TopdownNode(
        name="Backend Bound",
        value=72.01,
        children=[
            TopdownNode(
                name="Memory Bound",
                value=38.89,
                children=[TopdownNode(name="L3 Bound", value=30.96)],
            ),
        ],
    )
    dumped = TopdownNode.model_dump_json(root)
    loaded = TopdownNode.model_validate_json(dumped)
    assert loaded.children[0].children[0].name == "L3 Bound"
    assert loaded.children[0].children[0].value == 30.96


def test_profile_topdown_tree_round_trips_json() -> None:
    # A populated recursive topdown_tree on Profile must survive JSON round-trip
    # (exercises list-of-recursive-model serialization on the Profile class).
    profile = Profile(
        metadata=ProfileMetadata(customer="acme", date="2026-08-21"),
        topdown_tree=[
            TopdownNode(
                name="Backend Bound",
                value=72.01,
                children=[
                    TopdownNode(
                        name="Memory Bound",
                        value=38.89,
                        children=[TopdownNode(name="L3 Bound", value=30.96)],
                    ),
                ],
            )
        ],
    )
    loaded = Profile.model_validate_json(profile.model_dump_json())
    assert loaded.topdown_tree is not None
    assert loaded.topdown_tree[0].children[0].children[0].name == "L3 Bound"
    assert loaded.topdown_tree[0].children[0].children[0].value == 30.96
