"""Tests for TopdownParser."""

import pathlib

import pydantic
import pytest  # type: ignore[import-not-found]

from ingestion.topdown_parser import TopdownParser

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"


def test_parse_json_topdown_l1() -> None:
    parser = TopdownParser()
    result = parser.parse_json(DATA_DIR / "sample_topdown.json")
    assert result.topdown is not None
    assert result.topdown.frontend_bound == 0.25
    assert result.topdown.backend_bound == 0.40
    assert result.topdown.bad_speculation == 0.10
    assert result.topdown.retiring == 0.25


def test_parse_json_topdown_l2() -> None:
    parser = TopdownParser()
    result = parser.parse_json(DATA_DIR / "sample_topdown.json")
    assert result.topdown_l2 is not None
    assert result.topdown_l2.frontend_bound is not None
    assert result.topdown_l2.frontend_bound.fetch_latency == 0.15
    assert result.topdown_l2.backend_bound is not None
    assert result.topdown_l2.backend_bound.memory_bound == 0.30


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
    assert result.topdown.frontend_bound == 0.25
    assert result.topdown.backend_bound == 0.40


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
