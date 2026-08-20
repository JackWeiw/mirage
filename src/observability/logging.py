"""Structured logging configuration using structlog.

The framework owns one logging style: every ``src/`` module pulls a logger via
``get_logger("<module>")``. structlog is configured exactly once, at an
application entry point (the example scripts), by calling
``configure_logging`` or ``configure_logging_from_env`` -- **before** the first
log line is emitted. Library code (``Pipeline`` etc.) never configures logging
itself, so importing it has no global side effects and unit tests stay
deterministic. ``cache_logger_on_first_use=True`` then freezes each logger with
that entry-time config; configure-once-before-logging is the contract.
"""

import logging
import os
from typing import Any

import structlog

# Truthy strings that flip a boolean env flag on.
_TRUTHY = {"1", "true", "yes", "on"}


def configure_logging(log_level: str = "INFO", json_output: bool = False) -> None:
    """Configure structlog for the framework.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR).
        json_output: If true, output JSON format; otherwise, human-readable console format.
    """
    # structlog renderer types don't share a common base; annotate as Any so the
    # processor list type-checks under both structlog-installed and stub-less envs.
    renderer: Any = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.UnicodeDecoder(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(log_level)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a structured logger with a named binding.

    Args:
        name: Logger name (typically module name).

    Returns:
        Bound structlog logger with component=name context.
    """
    return structlog.get_logger().bind(component=name)


def configure_logging_from_env() -> None:
    """Configure structlog from operator env vars.

    Reads ``MIRAGE_LOG_LEVEL`` (default ``INFO``, case-insensitive; an invalid
    value raises ``ValueError`` loudly at load, mirroring the
    ``MIRAGE_AGENT_MAX_TOKENS`` validator pattern) and ``MIRAGE_LOG_JSON``
    (default off; truthy ``1/true/yes/on`` flips on JSON output), then calls
    :func:`configure_logging`. Intended as the single call at the top of an
    application entry point, before any log is emitted.
    """
    log_level = os.environ.get("MIRAGE_LOG_LEVEL", "INFO").upper()
    # Raise loudly on a typo rather than silently falling back to a default --
    # a misnamed level would otherwise suppress or flood the log stream.
    if not hasattr(logging, f"{log_level}"):
        raise ValueError(f"MIRAGE_LOG_LEVEL={log_level!r} is not a valid logging level")
    json_output = os.environ.get("MIRAGE_LOG_JSON", "").strip().lower() in _TRUTHY
    configure_logging(log_level=log_level, json_output=json_output)
