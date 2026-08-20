"""CollectionConfig loads collection.yaml and exposes typed fields used by both
the reference collector and the driver's synthetic collect callable."""

import pathlib

import collect_common  # type: ignore[import-not-found]
import pytest

_SCENARIO = pathlib.Path(__file__).resolve().parents[2] / "examples" / "scenarios" / "memory_bound"


def test_collection_config_loads_typed_fields() -> None:
    cfg = collect_common.CollectionConfig.from_yaml(_SCENARIO / "collection.yaml")
    assert cfg.duration_seconds == 20
    assert cfg.interval_seconds == 3
    assert cfg.perf_freq == 99
    assert cfg.cpu_mask == "0-63"
    assert cfg.numa_node == "0"
    assert cfg.per_worker_buffer_mb == 64
    assert cfg.warmup_seconds == 5
    assert cfg.measurement_seconds == 20
    assert cfg.llc_miss_floor_pct == 90.0


def test_numactl_prefix_orders_numactl_before_taskset() -> None:
    prefix = collect_common.numactl_taskset_prefix(cpu_mask="4-7", numa_node="1")
    # numactl must wrap taskset so BOTH cpu + memory bind apply to the binary.
    assert prefix[:2] == ["numactl", "--cpunodebind=1"]
    assert "--membind=1" in prefix
    i_taskset = prefix.index("taskset")
    assert prefix[i_taskset + 1] == "-c"
    assert prefix[i_taskset + 2] == "4-7"


def test_synthetic_collect_launches_with_numactl_and_collects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """The synthetic collect callable mirrors run_and_collect but prepends numactl.
    Asserts the launched argv carries numactl+taskset and collect_topdown is invoked."""
    cfg = collect_common.CollectionConfig(
        duration_seconds=20,
        interval_seconds=3,
        perf_freq=99,
        cpu_mask="0-3",
        numa_node="0",
        per_worker_buffer_mb=8,
        warmup_seconds=0,
        measurement_seconds=1,
    )
    captured: dict[str, object] = {}

    class _FakeProc:
        pid = 4242

        def poll(self) -> int | None:
            return None  # alive through warmup

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            return None

    def fake_popen(cmd: list[str], **_kw: object) -> _FakeProc:
        captured["cmd"] = cmd
        return _FakeProc()

    class _Coll:
        success = True
        topdown_path = str(tmp_path / "topdown.txt")
        error = ""

    fake_metrics = type("M", (), {})()
    fake_metrics.collect_topdown = lambda out, duration, interval, pid: _Coll()
    fake_metrics.parse_topdown_file = lambda p: _make_profile()

    monkeypatch.setattr(collect_common.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(collect_common.time, "sleep", lambda _s: None)

    # config.json must already exist (codegen writes it; run_and_collect assumes it).
    (tmp_path / "config.json").write_text("{}")
    binary = tmp_path / "wk"
    binary.write_text("#!/bin/sh\n")

    prof = collect_common.synthetic_collect(
        str(binary),
        {"config": {"warmup_seconds": 0, "measurement_seconds": 1}},
        cfg=cfg,
        metrics=fake_metrics,
        project_dir=tmp_path,
    )
    cmd = list(captured["cmd"])  # type: ignore[call-overload]
    assert "numactl" in cmd
    assert "taskset" in cmd
    assert prof is not None  # returned a Profile


def _make_profile() -> object:
    from profile.profile_schema import Profile, ProfileMetadata, TopdownL1

    return Profile(
        metadata=ProfileMetadata(customer="devkit", date="unknown"),
        topdown=TopdownL1(
            frontend_bound=10.0, backend_bound=65.0, bad_speculation=5.0, retiring=20.0
        ),
    )
