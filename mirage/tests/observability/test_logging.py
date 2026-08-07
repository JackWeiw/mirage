"""Tests for structured logging configuration."""

from observability.logging import configure_logging, get_logger


def test_configure_logging_default() -> None:
    configure_logging()
    log = get_logger("test")
    # Should not raise
    log.info("test message")


def test_configure_logging_json() -> None:
    configure_logging(log_level="DEBUG", json_output=True)
    log = get_logger("test")
    log.info("json test")


def test_get_logger_has_component() -> None:
    log = get_logger("my_module")
    # structlog bound logger should work
    assert log is not None
