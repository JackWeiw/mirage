"""Tests for MetricsCollector."""

import json
import pathlib
import tempfile

from harness.metrics_collector import MetricsCollector

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"


def test_collection_result_success() -> None:
    from models.results import CollectionResult

    result = CollectionResult(
        success=True, topdown_path="/tmp/topdown.json", flamegraph_path="/tmp/flamegraph.txt"
    )
    assert result.success is True
    assert result.topdown_path is not None


def test_collection_result_failure() -> None:
    from models.results import CollectionResult

    result = CollectionResult(success=False, error="perf not found")
    assert result.success is False


def test_collector_parse_existing_topdown() -> None:
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    topdown_file = tmp_dir / "topdown.json"
    topdown_data = {
        "topdown_l1": {
            "frontend_bound": 0.22,
            "backend_bound": 0.38,
            "bad_speculation": 0.11,
            "retiring": 0.29,
        },
        "memory": {"bandwidth_gbps": 43.8, "l3_miss_rate": 0.07},
    }
    topdown_file.write_text(json.dumps(topdown_data))
    collector = MetricsCollector()
    profile = collector.parse_topdown_file(topdown_file)
    assert profile.topdown is not None
    assert profile.topdown.frontend_bound == 0.22
    assert profile.memory is not None
    assert profile.memory.bandwidth_gbps == 43.8


def test_collector_parse_existing_flamegraph() -> None:
    collector = MetricsCollector()
    hotspots = collector.parse_flamegraph_file(DATA_DIR / "sample_flamegraph_folded.txt")
    assert len(hotspots) > 0
    assert hotspots[0].self_pct > 0


def test_collector_collect_topdown_no_devkit() -> None:
    collector = MetricsCollector(devkit_cmd=None)
    result = collector.collect_topdown(pathlib.Path("/tmp/out.json"))
    assert result.success is False
    assert "devkit_cmd not configured" in result.error


def test_collect_flamegraph_no_perf_returns_failure(tmp_path: pathlib.Path) -> None:
    # perf is not available in the test environment -> FileNotFoundError -> failure
    collector = MetricsCollector(perf_cmd="perf-not-a-real-binary")
    result = collector.collect_flamegraph(tmp_path / "fg.txt", duration=1)
    assert result.success is False
    assert result.error


def test_stackcollapse_converts_perf_script_to_folded() -> None:
    perf_script = "main\nprocess\nfolly::then\n\nmain\nprocess\n"
    folded = MetricsCollector._stackcollapse(perf_script)
    assert "main;process;folly::then 1" in folded
    assert "main;process 1" in folded
