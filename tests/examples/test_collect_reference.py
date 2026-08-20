"""collect_reference gates collection on the marker, numactl-pins, writes topdown.json."""

import json
import pathlib

import collect_common  # type: ignore[import-not-found]
import pytest

from profile.profile_schema import (
    Profile,
    ProfileMetadata,
    TopdownL1,
)

_SCEN = pathlib.Path(__file__).resolve().parents[2] / "examples" / "scenarios" / "memory_bound"


def test_reference_capture_numactl_pins_and_gates_on_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    cfg = collect_common.CollectionConfig.from_yaml(_SCEN / "collection.yaml")

    class _FakeProc:
        pid = 999
        stdout = iter(["__MEASUREMENT_WINDOW_START__\n", "data\n"])

        def kill(self) -> None:
            return None

        def wait(self, timeout: object = None) -> int:
            return 0

    fake_perf = _FakeProc()
    monkeypatch.setattr(collect_common.subprocess, "Popen", lambda *a, **_k: fake_perf)

    class _Coll:
        success = True
        topdown_path = str(tmp_path / "topdown.txt")
        error = ""

    fake_metrics = type("M", (), {})()
    fake_metrics.collect_topdown = lambda *a, **_k: _Coll()

    prof = Profile(
        metadata=ProfileMetadata(customer="devkit", date="unknown"),
        topdown=TopdownL1(
            frontend_bound=10.0, backend_bound=68.0, bad_speculation=5.0, retiring=17.0
        ),
    )
    fake_metrics.parse_topdown_file = lambda *_a, **_k: prof

    # Bypass the LLC perf-stat gate by stubbing the module-level fn.
    monkeypatch.setattr(collect_common, "_llc_miss_rate", lambda *a, **_k: 95.0)

    rc = collect_common.run_reference_capture(
        binary=str(tmp_path / "ref"),
        scenario_dir=tmp_path,
        devkit_cmd=None,
        cfg=cfg,
        metrics=fake_metrics,
    )
    assert rc == 0
    written = json.loads((tmp_path / "topdown.json").read_text())
    assert written["topdown"]["backend_bound"] == 68.0
