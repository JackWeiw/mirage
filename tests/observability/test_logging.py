"""Tests for structured logging configuration.

Covers level filtering, ``component`` binding, JSON/console output shape, and the
env-driven entry helper -- replacing the prior smoke tests with real assertions.
"""

import json
from typing import Any

import pytest
import structlog

from observability.logging import configure_logging, configure_logging_from_env, get_logger


def _captured_text(capsys: pytest.CaptureFixture[str]) -> str:
    """Join stdout+stderr -- structlog's PrintLogger may target either stream."""
    captured = capsys.readouterr()
    return f"{captured.out}{captured.err}"


def test_get_logger_binds_component() -> None:
    """``get_logger(name)`` binds ``component=name`` onto every emitted record."""
    log = get_logger("pipeline")
    with structlog.testing.capture_logs() as cap:
        log.info("build_succeeded", binary="workload")
    assert len(cap) == 1
    assert cap[0]["component"] == "pipeline"
    assert cap[0]["event"] == "build_succeeded"
    assert cap[0]["binary"] == "workload"


def test_level_filtering_suppresses_below_threshold(capsys: pytest.CaptureFixture[str]) -> None:
    """A WARNING-level config must drop DEBUG/INFO events before they render."""
    configure_logging(log_level="WARNING")
    log = get_logger("level_filter_probe")
    log.debug("debug_dropped")
    log.info("info_dropped")
    log.warning("warn_emitted")
    text = _captured_text(capsys)
    assert "debug_dropped" not in text
    assert "info_dropped" not in text
    assert "warn_emitted" in text


def test_console_output_shape(capsys: pytest.CaptureFixture[str]) -> None:
    """Default (console) renderer emits a human-readable line with the event name."""
    configure_logging(log_level="INFO")
    log = get_logger("console_probe")
    log.info("build_succeeded", binary="workload")
    text = _captured_text(capsys)
    assert "build_succeeded" in text
    assert "workload" in text  # the bound kwarg appears in the rendered line


def test_json_output_shape(capsys: pytest.CaptureFixture[str]) -> None:
    """JSON renderer emits one parseable JSON object with level/event/component."""
    configure_logging(log_level="INFO", json_output=True)
    log = get_logger("json_probe")
    log.info("build_succeeded", binary="workload")
    line = _captured_text(capsys).strip()
    record: dict[str, Any] = json.loads(line)
    assert record["event"] == "build_succeeded"
    assert record["level"] == "info"
    assert record["component"] == "json_probe"
    assert record["binary"] == "workload"


def test_configure_logging_from_env_level(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``MIRAGE_LOG_LEVEL=DEBUG`` makes DEBUG events render (default INFO drops them)."""
    monkeypatch.setenv("MIRAGE_LOG_LEVEL", "DEBUG")
    monkeypatch.delenv("MIRAGE_LOG_JSON", raising=False)
    configure_logging_from_env()
    log = get_logger("env_level_probe")
    log.debug("debug_event")
    assert "debug_event" in _captured_text(capsys)


def test_configure_logging_from_env_default_suppresses_debug(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no env override, the INFO default suppresses DEBUG events."""
    monkeypatch.delenv("MIRAGE_LOG_LEVEL", raising=False)
    monkeypatch.delenv("MIRAGE_LOG_JSON", raising=False)
    configure_logging_from_env()
    log = get_logger("env_default_probe")
    log.debug("debug_dropped")
    log.info("info_emitted")
    text = _captured_text(capsys)
    assert "debug_dropped" not in text
    assert "info_emitted" in text


def test_configure_logging_from_env_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``MIRAGE_LOG_JSON`` truthy flips on the JSON renderer."""
    monkeypatch.setenv("MIRAGE_LOG_LEVEL", "INFO")
    monkeypatch.setenv("MIRAGE_LOG_JSON", "yes")
    configure_logging_from_env()
    log = get_logger("env_json_probe")
    log.info("emitted")
    line = _captured_text(capsys).strip()
    record: dict[str, Any] = json.loads(line)
    assert record["event"] == "emitted"
    assert record["level"] == "info"


def test_configure_logging_from_env_truthy_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """All documented truthy spellings flip JSON output on."""
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        capsys.readouterr()  # flush any prior output
        monkeypatch.setenv("MIRAGE_LOG_LEVEL", "INFO")
        monkeypatch.setenv("MIRAGE_LOG_JSON", truthy)
        configure_logging_from_env()
        log = get_logger("truthy_probe")
        log.info("ok")
        record: dict[str, Any] = json.loads(_captured_text(capsys).strip())
        assert record["event"] == "ok", f"truthy={truthy!r} did not produce JSON"


def test_configure_logging_from_env_invalid_level_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invalid level name raises loudly rather than silently defaulting."""
    monkeypatch.setenv("MIRAGE_LOG_LEVEL", "BOGUS")
    with pytest.raises(ValueError, match="MIRAGE_LOG_LEVEL"):
        configure_logging_from_env()
