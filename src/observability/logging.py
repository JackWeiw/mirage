"""Structured logging configuration using structlog."""

import logging

import structlog


def configure_logging(log_level: str = "INFO", json_output: bool = False) -> None:
    """Configure structlog for the framework.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR).
        json_output: If true, output JSON format; otherwise, human-readable console format.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer(),
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
