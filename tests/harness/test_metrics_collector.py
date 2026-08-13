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


def test_stackcollapse_excludes_headers_and_strips_addr_dso_offset() -> None:
    # Realistic perf-script output: a sample header (comm pid [cpu] ts: event:)
    # followed by indented call-chain lines "addr sym+offset (dso)". perf prints
    # the leaf (IP) first, so file order is leaf -> root; this converter emits
    # frames in that same file order (no reversal).
    perf_script = (
        "swapper 0 [000] 12345.678:  cpu-clock:\n"
        "\t7ffffffbe012 folly::then+0x20 (/lib/libfolly.so)\n"
        "\t7ffffffbe000 main+0x40 (/app/app.exe)\n"
        "\n"
        "swapper 0 [000] 12345.999:  cpu-clock:\n"
        "\t7ffffffbe000 main+0x40 (/app/app.exe)\n"
        "\t7ffffffbdff0 [unknown] ([unknown])\n"
        "\n"
        "swapper 0 [001] 12346.111:  cpu-clock:\n"
        "\t7ffffffbe012 folly::then+0x20 (/lib/libfolly.so)\n"
        "\t7ffffffbe000 main+0x40 (/app/app.exe)\n"
    )
    folded = MetricsCollector._stackcollapse(perf_script)

    # Header text must never leak into a frame.
    for forbidden in ("swapper", "cpu-clock", "12345", "12346", "[000]", "[001]"):
        assert forbidden not in folded

    # Addresses, dso annotations, and offsets must be stripped from frames.
    assert "7ffffffbe012" not in folded
    assert "7ffffffbe000" not in folded
    assert "7ffffffbdff0" not in folded
    assert "(/" not in folded
    assert "+0x" not in folded

    lines = folded.splitlines()
    # Samples 1 and 3 share an identical stack -> merged with summed count 2.
    assert "folly::then;main 2" in lines
    # Sample 2 is a distinct stack -> count 1, [unknown] kept as a clean frame.
    assert "main;[unknown] 1" in lines
    assert len(lines) == 2


def test_stackcollapse_merges_bare_duplicate_stacks() -> None:
    # Synthetic (no addr/dso) input: two identical bare stacks must merge.
    perf_script = "main\nprocess\n\nmain\nprocess\n\nmain\nprocess\n"
    folded = MetricsCollector._stackcollapse(perf_script)
    assert folded == "main;process 3"


def test_stackcollapse_handles_missing_trailing_blank() -> None:
    perf_script = "main\nfolly::then"
    folded = MetricsCollector._stackcollapse(perf_script)
    assert folded == "main;folly::then 1"
