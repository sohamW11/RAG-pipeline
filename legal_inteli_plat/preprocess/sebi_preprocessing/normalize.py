"""Normalize raw parser outputs into DocumentElement[] and attach provenance.

Downstream code must never branch on which parser produced an element (CLAUDE.md
§4 "one schema out"), so this is the single place Docling's raw labels become the
schema's ``type`` and every element receives ``doc_id``, ``part``, ``source_file``,
``page``, ``bbox``, ``source_parser``. Elements without a bbox never reach here —
:mod:`parse_native` drops them at the source.
"""

from __future__ import annotations

from .models import BBox, DocumentElement, ElementType
from .parse_native import RawElement

# Docling DocItemLabel -> schema ElementType (README §2 "Docling label -> schema").
# Unknown labels fall back to "paragraph" (the safe, lossless-text default).
LABEL_TO_TYPE: dict[str, ElementType] = {
    "title": "heading",
    "section_header": "heading",
    "text": "paragraph",
    "paragraph": "paragraph",
    "footnote": "paragraph",
    "code": "paragraph",
    "formula": "paragraph",
    "list_item": "list",
    "list": "list",
    "table": "table",
    "picture": "figure",
    "figure": "figure",
    "caption": "caption",
    "page_header": "header_footer",
    "page_footer": "header_footer",
}


def map_label(label: str) -> ElementType:
    """Map a Docling label to a schema element type; unknown -> ``paragraph``."""
    return LABEL_TO_TYPE.get(label, "paragraph")


def build_element(
    *,
    raw: RawElement,
    doc_id: str,
    part: int,
    source_file: str,
    table_grid: list[list[str]] | None = None,
    source_parser: str | None = None,
    notes: str | None = None,
    confidence: float | None = None,
) -> DocumentElement:
    """Convert one raw element to a provenance-tagged ``DocumentElement``.

    ``element_id`` is stable and reconstructable: ``{doc_id}:{part}:p{page}:{order}``.
    For tables, ``table_grid``/``source_parser``/``notes`` carry the gate+repair
    outcome (Camelot may have replaced Docling's grid); for text elements they are
    ignored and the raw text is used.
    """
    etype = map_label(raw.label)
    is_table = etype == "table"
    return DocumentElement(
        element_id=f"{doc_id}:{part}:p{raw.page}:{raw.reading_order}",
        doc_id=doc_id,
        part=part,
        source_file=source_file,
        page=raw.page,
        reading_order=raw.reading_order,
        type=etype,
        text=None if is_table else raw.text,
        table=(table_grid if table_grid is not None else raw.table) if is_table else None,
        bbox=BBox(x0=raw.bbox.x0, y0=raw.bbox.y0, x1=raw.bbox.x1, y1=raw.bbox.y1),
        source_parser=source_parser or raw.source_parser,  # type: ignore[arg-type]
        confidence=confidence if confidence is not None else raw.confidence,
        notes=notes,
    )
