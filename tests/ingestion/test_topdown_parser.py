"""Tests for TopdownParser."""

import pathlib

import pydantic
import pytest

from ingestion.topdown_parser import TopdownParser

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"


def test_parse_json_topdown_l1() -> None:
    parser = TopdownParser()
    result = parser.parse_json(DATA_DIR / "sample_topdown.json")
    assert result.topdown is not None
    # Topdown L1 values are PERCENTAGES (0-100), matching parse_text.
    assert result.topdown.frontend_bound == 25.0
    assert result.topdown.backend_bound == 40.0
    assert result.topdown.bad_speculation == 10.0
    assert result.topdown.retiring == 25.0


def test_parse_json_topdown_l2() -> None:
    parser = TopdownParser()
    result = parser.parse_json(DATA_DIR / "sample_topdown.json")
    assert result.topdown_l2 is not None
    assert result.topdown_l2.frontend_bound is not None
    assert result.topdown_l2.frontend_bound.fetch_latency == 15.0
    assert result.topdown_l2.backend_bound is not None
    assert result.topdown_l2.backend_bound.memory_bound == 30.0


def test_parse_json_memory() -> None:
    parser = TopdownParser()
    result = parser.parse_json(DATA_DIR / "sample_topdown.json")
    assert result.memory is not None
    assert result.memory.bandwidth_gbps == 45.2
    assert result.memory.l3_miss_rate == 0.08


def test_parse_csv_topdown_l1() -> None:
    parser = TopdownParser()
    result = parser.parse_csv(DATA_DIR / "sample_topdown.csv")
    assert result.topdown is not None
    assert result.topdown.frontend_bound == 25.0
    assert result.topdown.backend_bound == 40.0


def test_parse_csv_memory() -> None:
    parser = TopdownParser()
    result = parser.parse_csv(DATA_DIR / "sample_topdown.csv")
    assert result.memory is not None
    assert result.memory.bandwidth_gbps == 45.2


def test_parse_json_file_not_found_raises() -> None:
    parser = TopdownParser()
    with pytest.raises(FileNotFoundError):
        parser.parse_json(DATA_DIR / "nonexistent.json")


def test_parse_json_malformed_raises_validation_error() -> None:
    parser = TopdownParser()
    with pytest.raises(pydantic.ValidationError):
        parser.parse_json(DATA_DIR / "malformed_topdown.json")


def test_parse_text_topdown_l1() -> None:
    # devkit `tuner top-down` emits a TEXT report; values are PERCENTAGES.
    parser = TopdownParser()
    result = parser.parse_text(DATA_DIR / "sample_topdown.txt")
    assert result.topdown is not None
    assert result.topdown.backend_bound == 72.01
    assert result.topdown.frontend_bound == 17.59
    assert result.topdown.bad_speculation == 3.01
    assert result.topdown.retiring == 7.38


def test_parse_text_no_l2_no_memory() -> None:
    parser = TopdownParser()
    result = parser.parse_text(DATA_DIR / "sample_topdown.txt")
    # The text report carries only L1; L2 and memory are not present.
    assert result.topdown_l2 is None
    assert result.memory is None


def test_parse_text_file_not_found_raises() -> None:
    parser = TopdownParser()
    with pytest.raises(FileNotFoundError):
        parser.parse_text(DATA_DIR / "nonexistent.txt")


def test_parse_text_no_l1_lines_raises() -> None:
    # A .txt with no recognizable L1 lines must surface, not return zeros.
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("nothing useful here\nno numbers\n")
        path = pathlib.Path(f.name)
    try:
        parser = TopdownParser()
        with pytest.raises(ValueError):
            parser.parse_text(path)
    finally:
        path.unlink()


def test_parse_text_averages_multiple_interval_reports() -> None:
    # The devkit emits one L1 block per -i interval; -d 6 -i 2 -> 3 blocks here.
    # parse_text must MEAN across blocks, not last-wins, so a multi-interval
    # capture reflects the steady-state rather than an arbitrary single block.
    import tempfile

    block = (
        "Backend Bound                    70.00\n"
        "Frontend Bound                   20.00\n"
        "Bad Speculation                   3.00\n"
        "Retiring                           7.00\n"
    )
    # Second block drifts: backend 74, frontend 16. Mean backend = 72.
    block2 = (
        "Backend Bound                    74.00\n"
        "Frontend Bound                   16.00\n"
        "Bad Speculation                   3.00\n"
        "Retiring                           7.00\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(block + "\n" + block2 + "\n")
        path = pathlib.Path(f.name)
    try:
        parser = TopdownParser()
        result = parser.parse_text(path)
        assert result.topdown is not None
        assert result.topdown.backend_bound == 72.0  # mean(70, 74)
        assert result.topdown.frontend_bound == 18.0  # mean(20, 16)
        assert result.topdown.bad_speculation == 3.0
        assert result.topdown.retiring == 7.0
    finally:
        path.unlink()
