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


def test_parse_text_summary_meaned_across_blocks() -> None:
    parser = TopdownParser()
    result = parser.parse_text(DATA_DIR / "sample_topdown_tree.txt")
    assert result.summary is not None
    # mean(100,000,000,000 ; 120,000,000,000) = 110,000,000,000
    assert result.summary.cycles == 110_000_000_000
    assert result.summary.instructions == 50_000_000_000
    # mean(0.45 ; 0.55) = 0.50
    assert abs(result.summary.ipc - 0.50) < 0.001


def test_parse_text_tree_structure_and_mean() -> None:
    parser = TopdownParser()
    result = parser.parse_text(DATA_DIR / "sample_topdown_tree.txt")
    assert result.topdown_tree is not None
    # 4 L1 roots, in report order.
    names = [n.name for n in result.topdown_tree]
    assert names == ["Bad Speculation", "Frontend Bound", "Retiring", "Backend Bound"]
    # L1 root values are meaned across the 2 blocks.
    backend = result.topdown_tree[3]
    assert abs(backend.value - 71.00) < 0.001  # mean(72.00, 70.00)
    # Backend -> [Core Bound, Memory Bound], in order.
    assert [c.name for c in backend.children] == ["Core Bound", "Memory Bound"]
    core = backend.children[0]
    assert abs(core.value - 32.00) < 0.001  # mean(33.00, 31.00)
    # Core -> Exe Ports Util -> [0 ports non serialize, 1 ports].
    exe = core.children[0]
    assert exe.name == "Exe Ports Util"
    assert [c.name for c in exe.children] == ["0 ports non serialize", "1 ports"]
    assert abs(exe.children[0].value - 17.50) < 0.001  # mean(18.00, 17.00)
    # Memory -> L3 Bound, meaned.
    memory = backend.children[1]
    l3 = next(c for c in memory.children if c.name == "L3 Bound")
    assert abs(l3.value - 32.00) < 0.001  # mean(31.00, 33.00)


def test_parse_text_tree_sampling_events() -> None:
    parser = TopdownParser()
    result = parser.parse_text(DATA_DIR / "sample_topdown_tree.txt")
    assert result.topdown_tree is not None
    backend = result.topdown_tree[3]
    memory = backend.children[1]
    mem_bound = next(c for c in memory.children if c.name == "Mem Bound")
    assert mem_bound.sampling_event == "cache-misses"
    # Category nodes have no sampling event.
    assert backend.sampling_event is None
    # Retiring carries inst_retired.
    retiring = next(n for n in result.topdown_tree if n.name == "Retiring")
    assert retiring.sampling_event == "inst_retired"
    # Branch Mispredicts carries br_mis_pred.
    bad_spec = result.topdown_tree[0]
    branch = bad_spec.children[0]
    assert branch.sampling_event == "br_mis_pred"
