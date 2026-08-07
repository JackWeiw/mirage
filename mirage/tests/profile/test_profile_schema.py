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


def test_topdown_l1_sums_approximately_to_one() -> None:
    td = TopdownL1(frontend_bound=0.25, backend_bound=0.40, bad_speculation=0.10, retiring=0.25)
    total = td.frontend_bound + td.backend_bound + td.bad_speculation + td.retiring
    assert abs(total - 1.0) < 0.01


def test_topdown_l2_nested() -> None:
    td_l2 = TopdownL2(
        frontend_bound=TopdownL2Frontend(fetch_latency=0.15, branch_detect=0.05),
        backend_bound=TopdownL2Backend(memory_bound=0.30, core_bound=0.10),
    )
    assert td_l2.frontend_bound is not None
    assert td_l2.frontend_bound.fetch_latency == 0.15


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
            frontend_bound=0.25, backend_bound=0.40, bad_speculation=0.10, retiring=0.25
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
