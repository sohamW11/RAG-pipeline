"""Docling wrapper — the main worker for native pages (CLAUDE.md §2).

Docling does the entire native page in one pass: text, layout, reading order,
AND tables. There is deliberately no "route native text to PyMuPDF" branch.
Produces raw elements (page + bbox + label + content) that :mod:`normalize`
maps to the ``DocumentElement`` schema and :mod:`tables` quality-gates.

**Coordinate convention.** Docling emits bboxes in a bottom-left origin; this
module converts every bbox to a **top-left origin, PDF points** (``x0,y0`` =
top-left corner, ``x1,y1`` = bottom-right, y increasing downward) so the native
and future scanned/OCR paths share one space. ``normalize`` copies these
straight into ``BBox``.

**Mixed PDFs.** Docling converts the whole file, but on a mixed document only the
*native* pages must go through it. Because scanned pages are non-contiguous
(e.g. page 146 of 153), we convert once and **filter the element stream** to the
native page set from triage — that enforces "native pages via Docling only" on
the output without fragile per-page conversion.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from docling.document_converter import DocumentConverter
from docling_core.types.doc import CoordOrigin, TableItem
from pydantic import BaseModel

from .config import PreprocessSettings, get_settings

if TYPE_CHECKING:
    from collections.abc import Iterable

log = structlog.get_logger(__name__)

# The DocumentConverter loads layout + TableFormer models (~507 MB, cached in
# ~/.cache/huggingface after first run). Build it once per process; tests and the
# pipeline may inject their own via the ``converter`` argument.
_CONVERTER: DocumentConverter | None = None


def get_converter() -> DocumentConverter:
    """Process-wide cached Docling converter (models load lazily on first use)."""
    global _CONVERTER
    if _CONVERTER is None:
        _CONVERTER = DocumentConverter()
    return _CONVERTER


class RawBBox(BaseModel):
    """Top-left origin, PDF points. ``x0,y0`` top-left; ``x1,y1`` bottom-right."""

    x0: float
    y0: float
    x1: float
    y1: float


class RawElement(BaseModel):
    """One element straight out of Docling, before normalization to the schema.

    ``label`` is Docling's raw ``DocItemLabel`` value (e.g. ``section_header``,
    ``text``, ``list_item``, ``table``, ``picture``); :mod:`normalize` maps it to
    the schema ``type``. ``reading_order`` is 0-based **within its page**, in
    Docling's reading order.
    """

    page: int  # 1-indexed
    reading_order: int
    label: str
    text: str | None = None
    table: list[list[str]] | None = None
    bbox: RawBBox
    source_parser: str = "docling"
    confidence: float | None = None


class NativeParseResult(BaseModel):
    """Raw Docling output for the native pages of one PDF."""

    source_file: str
    pages_parsed: list[int]  # native pages that produced elements, sorted
    elements: list[RawElement]
    tables_found: int


def _to_topleft_bbox(bbox, page_height: float) -> RawBBox:
    """Convert a Docling bbox to top-left origin, PDF points.

    In a bottom-left origin, ``t`` is the top edge (larger y) and ``b`` the
    bottom (smaller y); flip both around the page height. Coordinates are then
    sorted so ``x0<=x1`` and ``y0<=y1`` regardless of source origin.
    """
    if bbox.coord_origin == CoordOrigin.TOPLEFT:
        xs, ys = (bbox.l, bbox.r), (bbox.t, bbox.b)
    else:  # BOTTOMLEFT
        xs, ys = (bbox.l, bbox.r), (page_height - bbox.t, page_height - bbox.b)
    x0, x1 = sorted(xs)
    y0, y1 = sorted(ys)
    return RawBBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _table_to_grid(table_item: TableItem) -> list[list[str]]:
    """Export a Docling table to a list-of-rows of stripped cell strings.

    On export failure return ``[]`` (an empty grid) — the table element is kept,
    never silently dropped (CLAUDE.md §5); the Checkpoint-2 gate flags it broken.
    """
    try:
        df = table_item.export_to_dataframe()
    except Exception as exc:  # noqa: BLE001 - one bad table must not kill the doc
        log.warning("parse_native.table_export_failed", error=str(exc))
        return []
    return [[("" if c is None else str(c)).strip() for c in row] for row in df.values.tolist()]


def parse_native(
    pdf_path: str | Path,
    native_pages: Iterable[int] | None = None,
    settings: PreprocessSettings | None = None,
    converter: DocumentConverter | None = None,
) -> NativeParseResult:
    """Run Docling on one PDF and return raw elements for its native pages.

    ``native_pages`` (1-indexed) restricts the output to those pages — pass the
    native set from triage on a mixed PDF; ``None`` keeps every page. Elements
    without provenance (no page/bbox) are dropped and logged, never emitted
    untagged (CLAUDE.md §4 "provenance everywhere").
    """
    settings = settings or get_settings()
    pdf_path = Path(pdf_path)
    keep = set(native_pages) if native_pages is not None else None
    converter = converter or get_converter()

    doc = converter.convert(str(pdf_path)).document
    page_heights = {no: page.size.height for no, page in doc.pages.items()}

    elements: list[RawElement] = []
    order_by_page: dict[int, int] = {}
    tables_found = 0

    for item, _level in doc.iterate_items():
        prov = item.prov[0] if getattr(item, "prov", None) else None
        if prov is None or prov.bbox is None:
            log.warning(
                "parse_native.skipped_untagged",
                source_file=pdf_path.name,
                label=str(getattr(getattr(item, "label", None), "value", "?")),
            )
            continue
        page_no = prov.page_no
        if keep is not None and page_no not in keep:
            continue

        bbox = _to_topleft_bbox(prov.bbox, page_heights.get(page_no, 0.0))
        label = str(getattr(item.label, "value", item.label))
        order = order_by_page.get(page_no, 0)
        order_by_page[page_no] = order + 1

        if isinstance(item, TableItem):
            tables_found += 1
            elements.append(
                RawElement(
                    page=page_no,
                    reading_order=order,
                    label=label,
                    table=_table_to_grid(item),
                    bbox=bbox,
                )
            )
        else:
            text = getattr(item, "text", None)
            elements.append(
                RawElement(
                    page=page_no,
                    reading_order=order,
                    label=label,
                    text=text or None,
                    bbox=bbox,
                )
            )

    result = NativeParseResult(
        source_file=pdf_path.name,
        pages_parsed=sorted(order_by_page),
        elements=elements,
        tables_found=tables_found,
    )
    log.info(
        "parse_native.done",
        source_file=result.source_file,
        pages_parsed=len(result.pages_parsed),
        elements=len(result.elements),
        tables_found=result.tables_found,
    )
    return result
