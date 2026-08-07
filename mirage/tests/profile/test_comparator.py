"""Tests for ProfileComparator."""

from config.framework_config import ComparisonConfig
from profile.comparator import ProfileComparator
from profile.profile_schema import (
    HotspotFunction,
    MemoryProfile,
    Profile,
    ProfileMetadata,
    TopdownL1,
)


def _make_customer_profile() -> Profile:
    return Profile(
        metadata=ProfileMetadata(customer="acme", date="2026-07-27"),
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
            ),
            HotspotFunction(
                function="CustomerCustom::featureCalc",
                library="custom",
                source="customer_custom",
                self_pct=8.0,
                cumulative_pct=20.0,
                call_path=["main", "CustomerCustom::featureCalc"],
            ),
        ],
        topdown=TopdownL1(
            frontend_bound=0.25, backend_bound=0.40, bad_speculation=0.10, retiring=0.25
        ),
        memory=MemoryProfile(bandwidth_gbps=45.2, l3_miss_rate=0.08),
    )


def _make_workload_profile() -> Profile:
    return Profile(
        metadata=ProfileMetadata(customer="workload_sim", date="2026-07-28"),
        hotspots=[
            HotspotFunction(
                function="folly::futures::detail::FutureImpl::then",
                library="folly",
                source="open_source",
                self_pct=11.0,
                cumulative_pct=33.0,
                call_path=[
                    "main",
                    "Server::handleRequest",
                    "folly::futures::detail::FutureImpl::then",
                ],
            ),
            HotspotFunction(
                function="folly::detail::ThreadPool::dispatch",
                library="folly",
                source="open_source",
                self_pct=3.0,
                cumulative_pct=8.0,
                call_path=["main", "Logger::flush", "folly::detail::ThreadPool::dispatch"],
            ),
        ],
        topdown=TopdownL1(
            frontend_bound=0.22, backend_bound=0.38, bad_speculation=0.11, retiring=0.29
        ),
        memory=MemoryProfile(bandwidth_gbps=43.8, l3_miss_rate=0.07),
    )


def test_compare_topdown_l1_diff() -> None:
    comparator = ProfileComparator(config=ComparisonConfig())
    report = comparator.compare(_make_customer_profile(), _make_workload_profile())
    td = report["topdown_l1"]
    assert td["frontend_bound"]["customer"] == 0.25
    assert td["frontend_bound"]["workload"] == 0.22
    assert abs(td["frontend_bound"]["diff_pct"] - (-12.0)) < 1.0


def test_compare_topdown_convergence() -> None:
    comparator = ProfileComparator(config=ComparisonConfig())
    report = comparator.compare(_make_customer_profile(), _make_workload_profile())
    # frontend_bound diff is -12% > threshold 10%, so NOT converged
    assert report["convergence"]["converged"] is False


def test_compare_hotspot_coverage() -> None:
    comparator = ProfileComparator(config=ComparisonConfig())
    report = comparator.compare(_make_customer_profile(), _make_workload_profile())
    # Customer has 1 open-source hotspot (FutureImpl::then)
    # Workload also has FutureImpl::then -> covered
    # Coverage = 1/1 = 100% > 80%
    assert report["hotspot_coverage"]["coverage_pct"] >= 80.0


def test_compare_memory_within_threshold() -> None:
    comparator = ProfileComparator(config=ComparisonConfig())
    report = comparator.compare(_make_customer_profile(), _make_workload_profile())
    # bandwidth: 43.8 vs 45.2, diff_pct = -3.1%, within 5%
    assert report["memory"]["bandwidth_gbps"]["within_threshold"] is True


def test_compare_none_topdown_graceful() -> None:
    comparator = ProfileComparator(config=ComparisonConfig())
    customer = Profile(metadata=ProfileMetadata(customer="a", date="2026-07-27"))
    workload = Profile(metadata=ProfileMetadata(customer="b", date="2026-07-28"))
    report = comparator.compare(customer, workload)
    assert report["topdown_l1"] == {}
    assert report["memory"] == {}
