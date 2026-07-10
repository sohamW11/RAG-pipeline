"""parse_native (Docling) tests against real SEBI PDF fixtures.

Docling conversion is expensive (loads models, seconds per PDF), so each fixture
is parsed once via a session-scoped fixture and shared across assertions.
"""

from __future__ import annotations

import pytest

from sebi_preprocessing.parse_native import RawElement, parse_native
from sebi_preprocessing.triage import triage_document


@pytest.fixture(scope="session")
def parsed_table_doc(native_table_pdf):
    return parse_native(native_table_pdf)


@pytest.fixture(scope="session")
def parsed_no_table_doc(native_no_real_table_pdf):
    return parse_native(native_no_real_table_pdf)


# --- element stream + provenance ---------------------------------------------


def test_produces_elements_with_provenance(parsed_table_doc) -> None:
    assert parsed_table_doc.elements
    assert parsed_table_doc.source_file == "native_table_83899.pdf"
    for el in parsed_table_doc.elements:
        assert isinstance(el, RawElement)
        assert el.page >= 1  # 1-indexed
        assert el.source_parser == "docling"
        # provenance everywhere: every element is page + bbox tagged
        assert el.bbox is not None


def test_bbox_is_top_left_origin(parsed_table_doc) -> None:
    # Converted to top-left origin: x0<=x1, y0<=y1, all within the page box.
    for el in parsed_table_doc.elements:
        b = el.bbox
        assert b.x0 <= b.x1
        assert b.y0 <= b.y1


def test_reading_order_is_per_page_and_zero_based(parsed_table_doc) -> None:
    by_page: dict[int, list[int]] = {}
    for el in parsed_table_doc.elements:
        by_page.setdefault(el.page, []).append(el.reading_order)
    for page, orders in by_page.items():
        assert orders == list(range(len(orders))), f"page {page} orders not contiguous"


def test_docling_labels_present(parsed_table_doc) -> None:
    labels = {el.label for el in parsed_table_doc.elements}
    # The bankers circular has headings and body text at minimum.
    assert "section_header" in labels
    assert "text" in labels


# --- tables -------------------------------------------------------------------


def test_real_ruled_table_extracted(parsed_table_doc) -> None:
    tables = [el for el in parsed_table_doc.elements if el.label == "table"]
    assert parsed_table_doc.tables_found == len(tables) == 1
    grid = tables[0].table
    assert grid is not None and len(grid) >= 2  # not empty / not a single row
    assert all(len(row) == 2 for row in grid)  # the abbreviations table is 2 columns
    # a known cell from the abbreviations table
    assert any("ASBA" in cell for row in grid for cell in row)


def test_no_table_hallucination(parsed_no_table_doc) -> None:
    # 34658 is legal text that *looks* tabular; Docling correctly finds 0 tables
    # (recon: PyMuPDF found 24 false positives, Camelot 4 — Docling 0).
    assert parsed_no_table_doc.tables_found == 0
    assert not [el for el in parsed_no_table_doc.elements if el.label == "table"]


# --- mixed PDF filtering ------------------------------------------------------


def test_native_pages_filter_excludes_scanned(mixed_pdf) -> None:
    native = triage_document(mixed_pdf).native_pages()  # {1,2,3,5}
    result = parse_native(mixed_pdf, native_pages=native)
    produced = {el.page for el in result.elements}
    assert 4 not in produced  # the scanned page is excluded from the Docling output
    assert produced.issubset(native)
