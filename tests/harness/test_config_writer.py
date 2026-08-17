"""Tests for write_config_json_atomic (crash-safe config rewrite)."""

import json
import os
import pathlib

import pytest

from harness.config_writer import write_config_json_atomic


def test_writes_valid_json_round_trip(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "config.json"
    write_config_json_atomic(target, {"thread_count": 8, "compute_ratio": 0.8})
    data = json.loads(target.read_text())
    assert data == {"thread_count": 8, "compute_ratio": 0.8}


def test_uses_temp_in_same_dir_then_replaces(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "config.json"
    target.write_text('{"old": true}')  # pre-existing live config

    replaces: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy_replace(src: str, dst: str) -> None:
        # The source must be a temp file in the SAME dir (same-filesystem
        # atomic rename), NOT the target itself.
        src_path = pathlib.Path(src)
        assert src_path.parent == target.parent
        assert src != str(target)
        assert src_path.name.startswith(".config.json")
        replaces.append((src, dst))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    write_config_json_atomic(target, {"thread_count": 4})

    assert len(replaces) == 1
    assert replaces[0][1] == str(target)
    # The temp file is gone (replaced onto the target).
    assert not any(p.name.startswith(".config.json") for p in tmp_path.iterdir())


def test_failure_mid_write_leaves_target_untouched(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "config.json"
    target.write_text('{"old": true}')

    # Simulate a crash mid-write: fsync raises. The target must keep its old
    # content and no orphaned temp file may remain.
    def boom(fd: int) -> None:
        raise OSError("simulated mid-write crash")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        write_config_json_atomic(target, {"thread_count": 4})

    # Target untouched (no in-place truncate).
    assert json.loads(target.read_text()) == {"old": True}
    # No orphaned temp file litters the dir.
    assert not any(p.name.startswith(".config.json") for p in tmp_path.iterdir())


def test_creates_parent_dir_if_missing(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "nested" / "deep" / "config.json"
    write_config_json_atomic(target, {"qps": 50})
    assert json.loads(target.read_text()) == {"qps": 50}
