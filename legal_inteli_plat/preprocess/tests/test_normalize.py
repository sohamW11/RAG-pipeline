"""normalize tests — pure label mapping + element construction (no PDF, fast)."""

from __future__ import annotations

import pytest

from sebi_preprocessing.models import DocumentElement
from sebi_preprocessing.normalize import build_element, map_label
from sebi_preprocessing.parse_native import RawBBox, RawElement


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("section_header", "heading"),
        ("title", "heading"),
        ("text", "paragraph"),
        ("footnote", "paragraph"),
        ("list_item", "list"),
        ("table", "table"),
        ("picture", "figure"),
        ("caption", "caption"),
        ("page_footer", "header_footer"),
        ("page_header", "header_footer"),
        ("something_unknown", "paragraph"),  # safe default
    ],
)
def test_map_label(label: str, expected: str) -> None:
    assert map_label(label) == expected


def _raw(label: str, *, text=None, table=None, page=2, order=3) -> RawElement:
    return RawElement(
        page=page,
        reading_order=order,
        label=label,
        text=text,
        table=table,
        bbox=RawBBox(x0=10, y0=20, x1=30, y1=40),
    )


def test_build_text_element_provenance() -> None:
    raw = _raw("section_header", text="CHAPTER I")
    el = build_element(raw=raw, doc_id="83899", part=0, source_file="f.pdf")
    assert isinstance(el, DocumentElement)
    assert el.element_id == "83899:0:p2:3"  # {doc_id}:{part}:p{page}:{order}
    assert el.type == "heading"
    assert el.text == "CHAPTER I"
    assert el.table is None
    assert el.source_parser == "docling"
    assert (el.bbox.x0, el.bbox.y0, el.bbox.x1, el.bbox.y1) == (10, 20, 30, 40)


def test_build_table_element_uses_repaired_grid_and_note() -> None:
    raw = _raw("table", table=[["a"]])  # original (broken) grid
    repaired = [["ASBA", "Application Supported by Blocked Amount"]]
    el = build_element(
        raw=raw,
        doc_id="83899",
        part=1,
        source_file="annex.pdf",
        table_grid=repaired,
        source_parser="camelot",
        notes="table repaired via camelot (lattice)",
    )
    assert el.type == "table"
    assert el.table == repaired  # repaired grid overrides the raw one
    assert el.text is None
    assert el.source_parser == "camelot"
    assert el.part == 1
    assert el.notes and "camelot" in el.notes
    assert el.element_id == "83899:1:p2:3"
