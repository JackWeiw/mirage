"""Tests for FlamegraphParser and FunctionClassifier."""

import pathlib

import pytest

from ingestion.flamegraph_parser import FlamegraphParser

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"


def test_parse_folded_extracts_hotspots() -> None:
    parser = FlamegraphParser()
    hotspots = parser.parse_folded(DATA_DIR / "sample_flamegraph_folded.txt")
    assert len(hotspots) > 0
    folly_funcs = [h for h in hotspots if h.library == "folly"]
    assert len(folly_funcs) >= 1


def test_parse_folded_extracts_call_paths() -> None:
    parser = FlamegraphParser()
    hotspots = parser.parse_folded(DATA_DIR / "sample_flamegraph_folded.txt")
    for h in hotspots:
        assert len(h.call_path) > 0


def test_parse_folded_classifies_open_source_vs_custom() -> None:
    parser = FlamegraphParser()
    hotspots = parser.parse_folded(DATA_DIR / "sample_flamegraph_folded.txt")
    open_source = [h for h in hotspots if h.source == "open_source"]
    custom = [h for h in hotspots if h.source == "customer_custom"]
    assert len(open_source) >= 1
    assert len(custom) >= 1


def test_parse_folded_cumulative_pct_greater_than_self_pct() -> None:
    parser = FlamegraphParser()
    hotspots = parser.parse_folded(DATA_DIR / "sample_flamegraph_folded.txt")
    for h in hotspots:
        assert h.cumulative_pct >= h.self_pct


def test_parse_folded_file_not_found_raises() -> None:
    parser = FlamegraphParser()
    with pytest.raises(FileNotFoundError):
        parser.parse_folded(DATA_DIR / "nonexistent.txt")


def test_parse_folded_malformed_file_raises() -> None:
    parser = FlamegraphParser()
    with pytest.raises(ValueError):
        parser.parse_folded(DATA_DIR / "malformed_flamegraph.txt")


def test_parse_stacks_preserves_per_path_counts(tmp_path: pathlib.Path) -> None:
    """parse_stacks returns raw (frames, count) lines without leaf aggregation."""
    fg = tmp_path / "f.txt"
    fg.write_text("main;a;b 10\nmain;a 5\n")
    parser = FlamegraphParser()
    stacks = parser.parse_stacks(fg)
    expected = [(["main", "a", "b"], 10), (["main", "a"], 5)]
    assert sorted((tuple(s), c) for s, c in stacks) == sorted((tuple(e), c) for e, c in expected)
