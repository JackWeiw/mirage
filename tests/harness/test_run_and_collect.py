"""Tests for Pipeline.run_and_collect (error model, monkeypatched)."""

import pathlib
import subprocess
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from harness.pipeline import Pipeline
from models.results import CollectionResult, RunFailure
from profile.profile_schema import Profile


class _FakeProc:
    """Stand-in for a Popen process."""

    def __init__(
        self,
        pid: int,
        poll_rc: int | None,
        wait_rc: int | None = 0,
        wait_raises_timeout: bool = False,
    ) -> None:
        self.pid = pid
        self._poll_rc = poll_rc
        self._wait_rc = wait_rc
        self._wait_raises = wait_raises_timeout
        self._killed = False
        self.returncode: int | None = poll_rc

    def poll(self) -> int | None:
        return self._poll_rc

    def wait(self, timeout: float | None = None) -> int:
        if self._wait_raises:
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout or 0)
        assert self._wait_rc is not None  # test invariant: wait_rc set when not raising
        return self._wait_rc

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        return ("out", "err")

    def kill(self) -> None:
        self._killed = True


def _make_pipeline(
    tmp_path: pathlib.Path,
    collect_impl: Callable[..., CollectionResult],
) -> Pipeline:
    pipe = Pipeline(tmp_path)
    pipe.metrics_collector.collect_topdown = collect_impl  # type: ignore[method-assign]
    return pipe


def test_run_and_collect_crash_during_warmup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    proc = _FakeProc(pid=999, poll_rc=139, wait_rc=139)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    pipe = _make_pipeline(
        tmp_path,
        collect_impl=lambda *a, **k: CollectionResult(success=False, error="unreachable"),
    )
    rf = pipe.run_and_collect(
        binary_path="/bin/x",
        project_dir=tmp_path,
        warmup_seconds=0,
        measurement_seconds=1,
    )
    assert isinstance(rf, RunFailure)
    assert rf.kind == "crash"


def test_run_and_collect_collect_failure_retries_then_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    proc = _FakeProc(pid=999, poll_rc=None, wait_rc=0)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    collect_mock = MagicMock(return_value=CollectionResult(success=False, error="devkit rc=1"))
    pipe = _make_pipeline(tmp_path, collect_impl=collect_mock)
    rf = pipe.run_and_collect(
        binary_path="/bin/x",
        project_dir=tmp_path,
        warmup_seconds=0,
        measurement_seconds=1,
    )
    assert isinstance(rf, RunFailure)
    assert rf.kind == "collect_fail"
    # collect_retry default is 1 -> 2 attempts total (1 + 1 retry).
    assert collect_mock.call_count >= 2


def test_run_and_collect_success_returns_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    proc = _FakeProc(pid=999, poll_rc=None, wait_rc=0)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    td_text = "backend bound 72.0\nfrontend bound 10.0\nbad speculation 5.0\nretiring 13.0\n"

    def _write_then_return(
        out_path: object,
        duration: int = 1,
        interval: int = 3,
        pid: int | None = None,
    ) -> CollectionResult:
        pathlib.Path(out_path).write_text(td_text)  # type: ignore[arg-type]
        return CollectionResult(success=True, topdown_path=str(out_path))

    pipe = _make_pipeline(tmp_path, collect_impl=_write_then_return)
    prof = pipe.run_and_collect(
        binary_path="/bin/x",
        project_dir=tmp_path,
        warmup_seconds=0,
        measurement_seconds=1,
    )
    assert isinstance(prof, Profile)
    assert prof.topdown is not None
    assert prof.topdown.backend_bound == 72.0
