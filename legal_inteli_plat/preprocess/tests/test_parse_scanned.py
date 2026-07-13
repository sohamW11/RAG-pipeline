"""OCR path tests against the real scanned fixture (Gazette of India scan).

OCR is slow (~20s/page), so a session-scoped adapter parses page 1 once and the
assertions share it. Uses the pure-pip RapidOCR engine (no system tesseract).
"""

from __future__ import annotations

import pytest

from sebi_preprocessing.config import OcrConfig, ParsersConfig, PreprocessSettings
from sebi_preprocessing.parse_scanned import (
    RapidOcrAdapter,
    _px_to_points,
    get_ocr_adapter,
    parse_scanned,
)


@pytest.fixture(scope="session")
def ocr_settings() -> PreprocessSettings:
    return PreprocessSettings(parsers=ParsersConfig(ocr=OcrConfig(adapter="rapidocr")))


@pytest.fixture(scope="session")
def ocr_adapter() -> RapidOcrAdapter:
    return RapidOcrAdapter()  # loads ONNX models once


@pytest.fixture(scope="session")
def scanned_page1(scanned_pdf, ocr_settings, ocr_adapter):
    return parse_scanned(scanned_pdf, [1], settings=ocr_settings, adapter=ocr_adapter)


# --- pure coordinate conversion ----------------------------------------------


def test_px_to_points_scales_by_dpi() -> None:
    # 300 px at 300 DPI == 72 points (1 inch)
    b = _px_to_points(0, 0, 300, 300, dpi=300)
    assert b.x0 == 0 and b.y0 == 0
    assert round(b.x1, 3) == 72.0 and round(b.y1, 3) == 72.0


# --- OCR on the real scan -----------------------------------------------------


def test_ocr_extracts_text(scanned_page1) -> None:
    assert scanned_page1.pages_parsed == [1]
    assert scanned_page1.elements  # produced text


def test_ocr_provenance(scanned_page1) -> None:
    for el in scanned_page1.elements:
        assert el.source_parser == "ocr"
        assert el.page == 1
        assert el.label == "text"
        assert 0.0 <= (el.confidence or 0) <= 1.0
        assert el.bbox.x0 <= el.bbox.x1 and el.bbox.y0 <= el.bbox.y1
        assert el.text and el.text.strip()


def test_ocr_reading_order_contiguous(scanned_page1) -> None:
    orders = [el.reading_order for el in scanned_page1.elements]
    assert orders == list(range(len(orders)))


def test_ocr_recovers_a_known_word(scanned_page1) -> None:
    # The Gazette of India masthead says "EXTRAORDINARY" (clean, high-conf line).
    blob = " ".join(el.text for el in scanned_page1.elements).lower()
    assert "extraordinary" in blob


# --- adapter plumbing ---------------------------------------------------------


def test_empty_scanned_pages_returns_empty(scanned_pdf, ocr_settings, ocr_adapter) -> None:
    result = parse_scanned(scanned_pdf, [], settings=ocr_settings, adapter=ocr_adapter)
    assert result.pages_parsed == [] and result.elements == []


def test_factory_selects_rapidocr(ocr_settings) -> None:
    adapter = get_ocr_adapter(ocr_settings)
    assert hasattr(adapter, "read")


def test_unknown_adapter_raises() -> None:
    import sebi_preprocessing.parse_scanned as ps

    ps._ADAPTER = None  # bypass the process cache for this check
    bad = PreprocessSettings(parsers=ParsersConfig(ocr=OcrConfig(adapter="nope")))
    with pytest.raises(ValueError, match="unknown ocr adapter"):
        get_ocr_adapter(bad)
    ps._ADAPTER = None
