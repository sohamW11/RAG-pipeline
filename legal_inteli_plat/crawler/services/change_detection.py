"""Change detection.

Decides whether a document seen during a crawl is new, unchanged, or changed
compared to what we already stored -- *before* spending bandwidth on a
download. The comparison is a pure function of two signatures, which keeps it
trivially unit-testable and free of I/O.

Signals are compared in order of decreasing reliability:

1. ``sha256``            -- content hash (definitive when both are known)
2. ``etag``              -- server-provided content identifier
3. ``last_modified``     -- server-provided modification time
4. ``publication_date``  -- metadata publication date
5. ``content_hash``      -- fingerprint of the metadata record
6. ``url``               -- last resort; a changed URL implies a new artefact
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class ChangeType(str, Enum):
    """Outcome of a change-detection evaluation."""

    NEW = "new"
    UNCHANGED = "unchanged"
    CHANGED = "changed"


@dataclass(frozen=True)
class ChangeSignature:
    """The comparable fingerprint of a document at a point in time."""

    url: Optional[str] = None
    sha256: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    publication_date: Optional[datetime] = None
    content_hash: Optional[str] = None


@dataclass(frozen=True)
class ChangeDecision:
    """The verdict plus a human-readable reason and a download recommendation."""

    change_type: ChangeType
    reason: str
    should_download: bool


class ChangeDetector:
    """Pure change-detection strategy over two :class:`ChangeSignature` values."""

    # (attribute name, human label) ordered by reliability.
    _SIGNALS: tuple[tuple[str, str], ...] = (
        ("sha256", "sha256"),
        ("etag", "etag"),
        ("last_modified", "last_modified"),
        ("publication_date", "publication_date"),
        ("content_hash", "content_hash"),
        ("url", "url"),
    )

    def evaluate(
        self, existing: Optional[ChangeSignature], candidate: ChangeSignature
    ) -> ChangeDecision:
        """Compare a candidate document against the stored version."""
        if existing is None:
            return ChangeDecision(ChangeType.NEW, "no prior version", should_download=True)

        compared_any = False
        for attr, label in self._SIGNALS:
            old = getattr(existing, attr)
            new = getattr(candidate, attr)
            if old is None or new is None:
                continue
            compared_any = True
            if old != new:
                return ChangeDecision(
                    ChangeType.CHANGED,
                    f"{label} differs ({old!r} -> {new!r})",
                    should_download=True,
                )
            # First mutually-present matching signal is authoritative.
            return ChangeDecision(
                ChangeType.UNCHANGED, f"{label} unchanged", should_download=False
            )

        # No overlapping signals to compare -> re-download to be safe.
        reason = "no comparable signals" if not compared_any else "signals equal"
        return ChangeDecision(ChangeType.CHANGED, reason, should_download=True)
