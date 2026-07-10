"""Structured JSON logging to ``logs/`` (CLAUDE.md §3), matching the Phase-1 stack."""

from __future__ import annotations

import logging
from pathlib import Path

import structlog

from .config import PreprocessSettings, get_settings


def configure_logging(settings: PreprocessSettings | None = None) -> None:
    """Route structlog to ``logs/preprocess.log`` as JSON lines (or key-value)."""
    settings = settings or get_settings()
    logs_dir = Path(settings.paths.logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, settings.app.log_level.upper(), logging.INFO)
    logging.basicConfig(
        filename=str(logs_dir / "preprocess.log"),
        level=level,
        format="%(message)s",
    )
    renderer = (
        structlog.processors.JSONRenderer()
        if settings.app.json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
