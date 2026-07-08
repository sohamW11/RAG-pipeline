"""Structured JSON logging.

The whole pipeline emits machine-parseable JSON log lines so that discovery,
downloads, retries, failures and skips can be shipped to a log aggregator and
queried. ``structlog`` renders the events; the standard library ``logging``
module remains the transport so third-party libraries integrate cleanly.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_CONFIGURED = False


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    """Configure process-wide structured logging.

    Args:
        level: Root log level name, e.g. ``"INFO"``.
        json_logs: When ``True`` emit JSON; otherwise a colourised console
            renderer (useful for local development).
    """
    global _CONFIGURED

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str, **initial_values: Any) -> structlog.stdlib.BoundLogger:
    """Return a bound structured logger, configuring logging lazily if needed."""
    if not _CONFIGURED:
        configure_logging()
    return structlog.get_logger(name).bind(logger=name, **initial_values)
