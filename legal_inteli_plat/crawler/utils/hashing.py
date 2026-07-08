"""Hashing helpers used by change detection and content addressing."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of a byte string."""
    return hashlib.sha256(data).hexdigest()


def sha256_hexdigest(data: bytes) -> str:
    """Alias of :func:`sha256_bytes` kept for readability at call sites."""
    return sha256_bytes(data)


def stable_hash(payload: dict[str, Any]) -> str:
    """Deterministically hash a JSON-serialisable payload.

    Keys are sorted so the digest is independent of dict ordering, which makes
    it usable as a change-detection fingerprint for metadata records.
    """
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
