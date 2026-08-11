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


# -- SVG (flamegraph.pl) parsing ----------------------------------------------


def test_parse_svg_extracts_hotspots() -> None:
    parser = FlamegraphParser()
    hotspots = parser.parse_folded(DATA_DIR / "sample_flamegraph.svg")
    funcs = {h.function for h in hotspots}
    assert funcs == {
        "main",
        "SearchService::process",
        "folly::futures::detail::FutureImpl::then",
        "CustomerCustom::hashFeature",
    }


def test_parse_svg_matches_equivalent_folded() -> None:
    """SVG reconstruction must yield the same self%/cum% as the equivalent folded file."""
    parser = FlamegraphParser()
    svg_hot = {h.function: h for h in parser.parse_folded(DATA_DIR / "sample_flamegraph.svg")}
    txt_hot = {
        h.function: h
        for h in parser.parse_folded(DATA_DIR / "sample_flamegraph_folded_equivalent.txt")
    }
    assert set(svg_hot) == set(txt_hot)
    for func in svg_hot:
        assert svg_hot[func].self_pct == pytest.approx(txt_hot[func].self_pct)
        assert svg_hot[func].cumulative_pct == pytest.approx(txt_hot[func].cumulative_pct)


def test_parse_svg_stacks_preserve_per_path_counts() -> None:
    parser = FlamegraphParser()
    stacks = parser.parse_stacks(DATA_DIR / "sample_flamegraph.svg")
    normalized = sorted((tuple(s), c) for s, c in stacks)
    expected = sorted(
        [
            (("main",), 200),
            (("main", "SearchService::process"), 370),
            (
                (
                    "main",
                    "SearchService::process",
                    "folly::futures::detail::FutureImpl::then",
                ),
                250,
            ),
            (("main", "SearchService::process", "CustomerCustom::hashFeature"), 180),
        ]
    )
    assert normalized == expected


def test_parse_svg_classifies_open_source_vs_custom() -> None:
    parser = FlamegraphParser()
    hotspots = parser.parse_folded(DATA_DIR / "sample_flamegraph.svg")
    open_source = {h.function for h in hotspots if h.source == "open_source"}
    custom = {h.function for h in hotspots if h.source == "customer_custom"}
    assert "folly::futures::detail::FutureImpl::then" in open_source
    assert "CustomerCustom::hashFeature" in custom


def test_parse_svg_cumulative_pct_greater_than_self_pct() -> None:
    parser = FlamegraphParser()
    hotspots = parser.parse_folded(DATA_DIR / "sample_flamegraph.svg")
    for h in hotspots:
        assert h.cumulative_pct >= h.self_pct


def test_parse_svg_malformed_raises() -> None:
    parser = FlamegraphParser()
    with pytest.raises(ValueError):
        parser.parse_folded(DATA_DIR / "malformed_flamegraph.svg")


def test_parse_svg_without_title_counts_uses_width() -> None:
    """When titles lack sample counts, counts are derived from rect width (ratios hold)."""
    parser = FlamegraphParser()
    svg_stacks = parser.parse_stacks(DATA_DIR / "sample_flamegraph_no_counts.svg")
    folded_stacks = parser.parse_stacks(DATA_DIR / "sample_flamegraph_folded_equivalent.txt")
    assert sorted((tuple(s), c) for s, c in svg_stacks) == sorted(
        (tuple(s), c) for s, c in folded_stacks
    )


def test_parse_svg_dispatched_by_suffix() -> None:
    """parse_stacks on .svg uses the SVG path, not the folded reader."""
    parser = FlamegraphParser()
    stacks = parser.parse_stacks(DATA_DIR / "sample_flamegraph.svg")
    # Every reconstructed stack is non-empty with a positive count.
    assert all(frames and count > 0 for frames, count in stacks)


# -- SVG robustness: realistic flamegraph.pl structure + edge cases ---------


def test_parse_svg_realistic_fixture_ignores_banner() -> None:
    """A real flamegraph.pl SVG has <style>/<defs>/a background <rect> and a banner
    <g> (no class) holding a <title>+<rect>; only func_g frames must be parsed."""
    parser = FlamegraphParser()
    hotspots = parser.parse_folded(DATA_DIR / "sample_flamegraph_realistic.svg")
    funcs = {h.function for h in hotspots}
    assert "Flame Graph" not in funcs  # banner group must be ignored
    assert funcs == {"main", "Service::run", "Compute::A", "Compute::B", "leafC"}


def test_parse_svg_realistic_fixture_stacks() -> None:
    """A 4-level chain with a multi-child intermediate node reconstructs correctly."""
    parser = FlamegraphParser()
    stacks = parser.parse_stacks(DATA_DIR / "sample_flamegraph_realistic.svg")
    normalized = sorted((tuple(s), c) for s, c in stacks)
    expected = sorted(
        [
            (("main",), 50),
            (("main", "Service::run"), 50),
            (("main", "Service::run", "Compute::A"), 200),
            (("main", "Service::run", "Compute::B"), 400),
            (("main", "Service::run", "Compute::A", "leafC"), 300),
        ]
    )
    assert normalized == expected


def test_parse_svg_skips_func_g_missing_rect(tmp_path: pathlib.Path) -> None:
    """A func_g with a title but no <rect> is skipped, not crashed on."""
    svg = tmp_path / "no_rect.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<g class="func_g"><title>only_title (10 samples, 1%)</title></g>'
        "</svg>"
    )
    parser = FlamegraphParser()
    assert parser.parse_stacks(svg) == []


def test_parse_svg_skips_rect_missing_x(tmp_path: pathlib.Path) -> None:
    """A func_g <rect> missing x is skipped rather than silently placed at x=0."""
    svg = tmp_path / "no_x.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<g class="func_g"><title>orphan (10 samples, 1%)</title>'
        '<rect y="48.0" width="50.0" height="16.0"/></g>'
        "</svg>"
    )
    parser = FlamegraphParser()
    assert parser.parse_stacks(svg) == []


def test_parse_svg_self_count_zero_emits_no_line(tmp_path: pathlib.Path) -> None:
    """A parent fully covered by its child has self count 0 and emits no line."""
    svg = tmp_path / "full_cover.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<g class="func_g"><title>main (100 samples, 100%)</title>'
        '<rect x="0.0" y="48.0" width="100.0" height="16.0"/></g>'
        '<g class="func_g"><title>child (100 samples, 100%)</title>'
        '<rect x="0.0" y="32.0" width="100.0" height="16.0"/></g>'
        "</svg>"
    )
    parser = FlamegraphParser()
    stacks = parser.parse_stacks(svg)
    assert sorted((tuple(s), c) for s, c in stacks) == [(("main", "child"), 100)]


def test_parse_stacks_missing_svg_raises_filenotfound() -> None:
    parser = FlamegraphParser()
    with pytest.raises(FileNotFoundError):
        parser.parse_stacks(DATA_DIR / "nonexistent.svg")
