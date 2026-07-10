"""Triage tests against real SEBI PDF fixtures (CLAUDE.md §2, robustness §8)."""

from __future__ import annotations

import pytest

from sebi_preprocessing.config import PreprocessSettings
from sebi_preprocessing.triage import (
    classify_page,
    render_scanned_page,
    render_scanned_pages,
    triage_document,
)


@pytest.fixture(scope="session")
def settings() -> PreprocessSettings:
    return PreprocessSettings()  # defaults: native_char_threshold=100, render_dpi=300


# --- pure classification rule -------------------------------------------------


@pytest.mark.parametrize(
    ("char_count", "expected"),
    [
        (0, "scanned"),
        (71, "scanned"),  # a real scanned page carried a 71-char text stamp
        (99, "scanned"),
        (100, "native"),  # threshold is inclusive
        (1231, "native"),
    ],
)
def test_classify_page(char_count: int, expected: str) -> None:
    assert classify_page(char_count, 100) == expected


# --- per-page triage on real PDFs ---------------------------------------------


def test_all_native_single_page(native_text_pdf, settings) -> None:
    result = triage_document(native_text_pdf, settings)
    assert result.page_count == 1
    assert result.native_pages() == {1}
    assert result.scanned_pages() == set()
    assert result.pages[0].char_count >= 100
    # geometry is carried for downstream bbox conversion
    assert result.pages[0].width > 0 and result.pages[0].height > 0


def test_all_native_with_table(native_table_pdf, settings) -> None:
    result = triage_document(native_table_pdf, settings)
    assert result.page_count == 3
    assert result.native_pages() == {1, 2, 3}


def test_fully_scanned(scanned_pdf, settings) -> None:
    result = triage_document(scanned_pdf, settings)
    assert result.page_count == 3
    assert result.scanned_pages() == {1, 2, 3}
    assert result.native_pages() == set()
    assert all(p.char_count < 100 for p in result.pages)


def test_mixed_pdf_is_per_page(mixed_pdf, settings) -> None:
    # The whole point of per-page triage: one scanned page inside native ones.
    result = triage_document(mixed_pdf, settings)
    assert result.page_count == 5
    assert result.scanned_pages() == {4}  # 0-indexed page 3 => 1-indexed page 4
    assert result.native_pages() == {1, 2, 3, 5}


def test_pages_are_one_indexed_and_ordered(native_table_pdf, settings) -> None:
    result = triage_document(native_table_pdf, settings)
    assert [p.page for p in result.pages] == [1, 2, 3]


# --- rendering the scanned path ----------------------------------------------


def test_render_scanned_page_returns_png(scanned_pdf, settings) -> None:
    png = render_scanned_page(scanned_pdf, page=1, settings=settings)
    assert isinstance(png, bytes)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic number


def test_render_scanned_pages_batch(scanned_pdf, settings) -> None:
    images = render_scanned_pages(scanned_pdf, pages=[1, 3], settings=settings)
    assert set(images) == {1, 3}
    assert all(v[:8] == b"\x89PNG\r\n\x1a\n" for v in images.values())
