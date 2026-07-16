"""Pydantic v2 schemas — the single output contract (CLAUDE.md §6).

Every element written to disk validates against these models. Downstream code
must never branch on which parser produced an element; provenance is carried on
the element itself, not encoded in the shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ElementType = Literal[
    "heading",
    "paragraph",
    "list",
    "table",
    "figure",
    "caption",
    "header_footer",
]
SourceParser = Literal["docling", "camelot", "ocr", "vlm"]
PageType = Literal["native", "scanned"]

SCHEMA_VERSION = "1.0"


class BBox(BaseModel):
    """Element bounding box in PDF points, page coordinate space."""

    x0: float
    y0: float
    x1: float
    y1: float


class DocumentElement(BaseModel):
    """One normalized piece of a document, fully provenance-tagged.

    ``element_id`` is stable and reconstructable: ``f"{doc_id}:{part}:p{page}:{order}"``.
    """

    element_id: str
    doc_id: str
    part: int = 0  # 0 = main PDF, 1+ = annexures
    source_file: str  # filename this element came from
    page: int
    reading_order: int
    type: ElementType
    text: str | None = None  # for text elements
    table: list[list[str]] | None = None  # rows of cells, for tables
    bbox: BBox
    source_parser: SourceParser
    confidence: float | None = None
    notes: str | None = None  # e.g. "table repaired via camelot"


class PageInfo(BaseModel):
    """Per-page triage + parse summary."""

    part: int = 0
    page: int
    page_type: PageType
    char_count: int  # extractable text chars found at triage
    element_count: int = 0
    tables_found: int = 0
    tables_repaired: int = 0


class ParsedDocument(BaseModel):
    """The output document: one JSON file per doc_id."""

    doc_id: str
    # Metadata joined from the Phase-1 inventory (crawler.db documents table):
    title: str | None = None
    date: str | None = None
    subsection: str | None = None
    source_url: str | None = None  # HTML landing page
    pdf_url: str | None = None  # direct, downloadable PDF
    metadata_matched: bool = True
    source_files: list[str] = Field(default_factory=list)  # main + annexures
    page_count: int = 0
    pages: list[PageInfo] = Field(default_factory=list)
    elements: list[DocumentElement] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)  # counts, timings
    errors: list[str] = Field(default_factory=list)
    parsed_at: datetime
    schema_version: str = SCHEMA_VERSION
