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
            frontend_bound=25.0, backend_bound=40.0, bad_speculation=10.0, retiring=25.0
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
            frontend_bound=22.0, backend_bound=38.0, bad_speculation=11.0, retiring=29.0
        ),
        memory=MemoryProfile(bandwidth_gbps=43.8, l3_miss_rate=0.07),
    )


def test_compare_topdown_l1_diff() -> None:
    comparator = ProfileComparator(config=ComparisonConfig())
    report = comparator.compare(_make_customer_profile(), _make_workload_profile())
    td = report["topdown_l1"]
    assert td["frontend_bound"]["customer"] == 25.0
    assert td["frontend_bound"]["workload"] == 22.0
    # absolute percentage points: 22.0 - 25.0 = -3.0 pp (NOT the -12% relative).
    assert abs(td["frontend_bound"]["diff_pct"] - (-3.0)) < 0.01


def test_compare_topdown_convergence() -> None:
    comparator = ProfileComparator(config=ComparisonConfig())
    report = comparator.compare(_make_customer_profile(), _make_workload_profile())
    # Every L1 metric is within 10pp (frontend -3pp, backend -2pp, bad_spec +1pp,
    # retiring +4pp), memory within 5%, coverage 100% -> converged.
    assert report["convergence"]["converged"] is True


def test_compare_topdown_nonconvergence_when_diff_exceeds_threshold() -> None:
    # A > 10pp gap on any L1 metric -> not converged (absolute-pp semantics).
    cust = Profile(
        metadata=ProfileMetadata(customer="a", date="2026-08-18"),
        topdown=TopdownL1(
            frontend_bound=25.0, backend_bound=40.0, bad_speculation=10.0, retiring=25.0
        ),
    )
    work = Profile(
        metadata=ProfileMetadata(customer="b", date="2026-08-18"),
        topdown=TopdownL1(
            frontend_bound=45.0, backend_bound=40.0, bad_speculation=10.0, retiring=5.0
        ),
    )
    rep = ProfileComparator().compare(cust, work)
    assert rep["topdown_l1"]["frontend_bound"]["diff_pct"] == 20.0  # 45 - 25
    assert rep["convergence"]["converged"] is False


def test_topdown_diff_pct_is_absolute_pp_not_relative() -> None:
    # The #46 regression: a small-customer-value metric (frontend 5.14) must NOT
    # blow up to a huge relative diff. 12.0 - 5.14 = 6.86 pp (absolute), NOT
    # (12-5.14)/5.14*100 = 133.5% relative.
    cust = Profile(
        metadata=ProfileMetadata(customer="a", date="2026-08-18"),
        topdown=TopdownL1(
            frontend_bound=5.14, backend_bound=72.79, bad_speculation=3.0, retiring=19.07
        ),
    )
    work = Profile(
        metadata=ProfileMetadata(customer="b", date="2026-08-18"),
        topdown=TopdownL1(
            frontend_bound=12.0, backend_bound=70.0, bad_speculation=3.0, retiring=15.0
        ),
    )
    rep = ProfileComparator().compare(cust, work)
    assert abs(rep["topdown_l1"]["frontend_bound"]["diff_pct"] - 6.86) < 0.01
    # 6.86pp is within a 10pp threshold (was 133% -> not within).
    assert rep["topdown_l1"]["frontend_bound"]["within_threshold"] is True


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
