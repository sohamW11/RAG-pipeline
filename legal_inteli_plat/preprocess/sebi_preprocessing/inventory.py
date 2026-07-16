"""Phase-1 inventory join (CLAUDE.md §2, Definition of Done #2).

Each parsed document carries forward ``title``, ``date``, ``subsection``, and
``source_url`` from the crawler's inventory. The crawler persists these to
``crawler.db`` (``documents`` + ``document_versions``); point
``paths.inventory_path`` at that DB (``*.db``) — a JSON export can be supported
the same way later. A PDF with no matching row is still parsed, flagged
``metadata_matched: false``.

The join key is the PDF filename (``document_versions.storage_key`` basename),
with a fallback to the doc_id (``documents.document_number``, the trailing number
in the filename).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import structlog
from pydantic import BaseModel

from .config import PreprocessSettings, get_settings

log = structlog.get_logger(__name__)


class InventoryRecord(BaseModel):
    doc_id: str  # documents.document_number
    title: str | None = None
    date: str | None = None  # publication_date (date part only)
    subsection: str | None = None  # documents.category_name
    source_url: str | None = None  # documents.source_url (HTML landing page)
    pdf_url: str | None = None  # documents.pdf_url (direct, downloadable PDF)


class Inventory:
    """In-memory lookup, keyed by filename with a doc_id fallback."""

    def __init__(
        self,
        by_filename: dict[str, InventoryRecord],
        by_doc_id: dict[str, InventoryRecord],
    ) -> None:
        self._by_filename = by_filename
        self._by_doc_id = by_doc_id

    def lookup(self, filename: str, doc_id: str) -> InventoryRecord | None:
        return self._by_filename.get(filename) or self._by_doc_id.get(doc_id)

    def __len__(self) -> int:
        return len(self._by_doc_id)


def _date_part(publication_date: str | None) -> str | None:
    if not publication_date:
        return None
    return publication_date.split(" ")[0].split("T")[0] or None


def load_inventory(settings: PreprocessSettings | None = None) -> Inventory | None:
    """Load the inventory from the configured crawler DB, or ``None`` if unset/missing."""
    settings = settings or get_settings()
    inv_path = settings.paths.inventory_path
    if not inv_path:
        return None
    path = Path(inv_path)
    if not path.exists():
        log.warning("inventory.missing", path=str(path))
        return None
    if path.suffix != ".db":
        log.warning("inventory.unsupported_format", path=str(path))
        return None

    by_filename: dict[str, InventoryRecord] = {}
    by_doc_id: dict[str, InventoryRecord] = {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT d.document_number, d.title, d.publication_date,
                   d.category_name, d.source_url, d.pdf_url, v.storage_key
            FROM documents d
            LEFT JOIN document_versions v ON v.document_id = d.id
            """
        )
        for r in rows:
            doc_id = str(r["document_number"]) if r["document_number"] is not None else None
            if not doc_id:
                continue
            record = InventoryRecord(
                doc_id=doc_id,
                title=r["title"],
                date=_date_part(r["publication_date"]),
                subsection=r["category_name"],
                source_url=r["source_url"],
                pdf_url=r["pdf_url"],
            )
            by_doc_id.setdefault(doc_id, record)
            if r["storage_key"]:
                by_filename[Path(r["storage_key"]).name] = record
    finally:
        conn.close()

    log.info("inventory.loaded", docs=len(by_doc_id), files=len(by_filename))
    return Inventory(by_filename, by_doc_id)
