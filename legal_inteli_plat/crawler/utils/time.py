"""Time helpers. Centralised so the whole service uses timezone-aware UTC."""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)
