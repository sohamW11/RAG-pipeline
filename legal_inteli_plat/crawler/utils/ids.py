"""Identifier helpers."""

from __future__ import annotations

import uuid


def new_uuid() -> str:
    """Return a new random UUID4 as a string."""
    return str(uuid.uuid4())
