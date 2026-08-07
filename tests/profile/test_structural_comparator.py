"""Tests for StructuralComparator call-path overlap."""

from profile.structural_comparator import StructuralComparator


def test_overlap_counts_trunk_stage_open_frames() -> None:
    customer = [
        (["main", "Svc::process", "StageA::run", "folly::X"], 100),
        (["main", "Svc::process", "StageB::run", "Customer::y"], 50),
    ]
    # workload matches trunk + stage A + folly::X but misses StageB
    workload = [(["main", "Svc::process", "StageA::run", "folly::X"], 80)]
    report = StructuralComparator().compare(customer, workload)
    assert report["trunk_present"] is True
    assert report["overall_overlap_pct"] < 100.0
    assert report["overall_overlap_pct"] > 50.0


def test_custom_leaf_frames_excluded_from_required() -> None:
    customer = [(["main", "Svc::process", "Customer::only"], 10)]
    workload = [(["main", "Svc::process"], 10)]
    report = StructuralComparator().compare(customer, workload)
    # Customer::only is a custom leaf -> not required; trunk matches fully
    assert report["overall_overlap_pct"] == 100.0


def test_empty_workload_reports_zero_overlap() -> None:
    customer = [(["main", "Svc::process", "folly::X"], 100)]
    report = StructuralComparator().compare(customer, [])
    assert report["overall_overlap_pct"] == 0.0
    assert report["trunk_present"] is False
