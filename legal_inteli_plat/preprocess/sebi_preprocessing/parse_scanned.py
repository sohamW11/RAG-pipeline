"""Scanned page parsing via a pluggable English OCR adapter (CLAUDE.md §2, §9).

Flow: a scanned page (detected by triage) is rendered to an image, the OCR
adapter reads it into text elements with bboxes + confidence, and those become
the same ``RawElement`` shape the native path produces — so :mod:`normalize` and
the pipeline treat native and scanned output identically.

The concrete engine sits behind ``OcrAdapter.read(...) -> list[RawElement]`` so it
is swappable and a multilingual adapter can drop in later without touching the
pipeline (§9 marks multilingual as a later phase — English only here).

Engines:
- :class:`TesseractAdapter` — the spec's named default; needs the system
  ``tesseract`` binary + ``pytesseract`` (production hosts).
- :class:`RapidOcrAdapter` — pure-pip ONNX engine (``rapidocr-onnxruntime``), no
  system binary; used where tesseract isn't installable.

Coordinate convention matches :mod:`parse_native`: bboxes in **top-left origin,
PDF points**. OCR runs on an image rendered at ``triage.render_dpi``, so a pixel
maps to points by ``pt = px * 72 / dpi`` (no y-flip — image space is already
top-left).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import structlog
from pydantic import BaseModel

from .config import PreprocessSettings, get_settings
from .parse_native import RawBBox, RawElement
from .triage import render_scanned_pages

if TYPE_CHECKING:
    from collections.abc import Iterable

log = structlog.get_logger(__name__)


class OcrAdapter(Protocol):
    """Swap point for the OCR engine. ``page`` is 1-indexed; ``dpi`` is the render
    DPI used so the adapter can map image pixels back to PDF points."""

    def read(self, image_png: bytes, *, page: int, dpi: int) -> list[RawElement]: ...


class ScannedParseResult(BaseModel):
    """OCR output for the scanned pages of one PDF (mirrors NativeParseResult)."""

    source_file: str
    pages_parsed: list[int]
    elements: list[RawElement]


def _px_to_points(x0: float, y0: float, x1: float, y1: float, dpi: int) -> RawBBox:
    scale = 72.0 / dpi
    return RawBBox(x0=x0 * scale, y0=y0 * scale, x1=x1 * scale, y1=y1 * scale)


class RapidOcrAdapter:
    """English OCR via ``rapidocr-onnxruntime`` (CPU, no system binary).

    Each detected text line becomes one ``text`` RawElement (label ``text`` ->
    schema ``paragraph``), ordered top-to-bottom then left-to-right.
    """

    def __init__(self, language: str = "eng") -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._language = language  # English-only for now (§9); kept for parity
        self._engine = RapidOCR()

    def read(self, image_png: bytes, *, page: int, dpi: int) -> list[RawElement]:
        import cv2
        import numpy as np

        arr = cv2.imdecode(np.frombuffer(image_png, np.uint8), cv2.IMREAD_COLOR)
        result, _ = self._engine(arr)
        lines = []
        for box, text, score in result or []:
            if not text or not text.strip():
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            lines.append((min(ys), min(xs), max(xs), max(ys), text.strip(), float(score)))
        lines.sort(key=lambda r: (r[0], r[1]))  # reading order: top-to-bottom, left-to-right

        elements: list[RawElement] = []
        for order, (y0, x0, x1, y1, text, score) in enumerate(lines):
            elements.append(
                RawElement(
                    page=page,
                    reading_order=order,
                    label="text",
                    text=text,
                    bbox=_px_to_points(x0, y0, x1, y1, dpi),
                    source_parser="ocr",
                    confidence=score,
                )
            )
        return elements


class TesseractAdapter:
    """English OCR via the system ``tesseract`` binary + ``pytesseract``.

    Groups word boxes into text lines (tesseract's block/paragraph/line indices)
    and emits one ``text`` RawElement per line with a mean word confidence.
    """

    def __init__(self, language: str = "eng") -> None:
        import pytesseract  # noqa: F401 - fail fast if the binding/binary is missing

        self._language = language

    def read(self, image_png: bytes, *, page: int, dpi: int) -> list[RawElement]:
        import io

        import pytesseract
        from PIL import Image

        img = Image.open(io.BytesIO(image_png))
        data = pytesseract.image_to_data(
            img, lang=self._language, output_type=pytesseract.Output.DICT
        )
        # Group words into lines by (block, paragraph, line) index.
        lines: dict[tuple, dict] = {}
        for i, word in enumerate(data["text"]):
            if not word or not word.strip():
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            if conf < 0:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            left, top = data["left"][i], data["top"][i]
            right, bottom = left + data["width"][i], top + data["height"][i]
            ln = lines.setdefault(
                key, {"words": [], "confs": [], "x0": left, "y0": top, "x1": right, "y1": bottom}
            )
            ln["words"].append(word.strip())
            ln["confs"].append(conf)
            ln["x0"] = min(ln["x0"], left)
            ln["y0"] = min(ln["y0"], top)
            ln["x1"] = max(ln["x1"], right)
            ln["y1"] = max(ln["y1"], bottom)

        ordered = sorted(lines.values(), key=lambda ln: (ln["y0"], ln["x0"]))
        elements: list[RawElement] = []
        for order, ln in enumerate(ordered):
            elements.append(
                RawElement(
                    page=page,
                    reading_order=order,
                    label="text",
                    text=" ".join(ln["words"]),
                    bbox=_px_to_points(ln["x0"], ln["y0"], ln["x1"], ln["y1"], dpi),
                    source_parser="ocr",
                    confidence=sum(ln["confs"]) / len(ln["confs"]) / 100.0,
                )
            )
        return elements


_ADAPTER: OcrAdapter | None = None


def get_ocr_adapter(settings: PreprocessSettings | None = None) -> OcrAdapter:
    """Build (and cache) the OCR adapter named in config (``parsers.ocr.adapter``)."""
    global _ADAPTER
    if _ADAPTER is not None:
        return _ADAPTER
    settings = settings or get_settings()
    name = settings.parsers.ocr.adapter.lower()
    lang = settings.parsers.ocr.language
    if name == "tesseract":
        _ADAPTER = TesseractAdapter(lang)
    elif name in ("rapidocr", "rapid", "rapidocr-onnxruntime"):
        _ADAPTER = RapidOcrAdapter(lang)
    else:
        raise ValueError(f"unknown ocr adapter: {name!r} (use 'tesseract' or 'rapidocr')")
    log.info("ocr.adapter_ready", adapter=name, language=lang)
    return _ADAPTER


def parse_scanned(
    pdf_path: str | Path,
    scanned_pages: Iterable[int],
    settings: PreprocessSettings | None = None,
    adapter: OcrAdapter | None = None,
) -> ScannedParseResult:
    """OCR the given scanned pages (1-indexed) of one PDF into RawElements."""
    settings = settings or get_settings()
    pdf_path = Path(pdf_path)
    pages = sorted(set(scanned_pages))
    if not pages:
        return ScannedParseResult(source_file=pdf_path.name, pages_parsed=[], elements=[])

    adapter = adapter or get_ocr_adapter(settings)
    dpi = settings.triage.render_dpi
    images = render_scanned_pages(pdf_path, pages, settings)

    elements: list[RawElement] = []
    parsed: list[int] = []
    for page in pages:
        page_elems = adapter.read(images[page], page=page, dpi=dpi)
        if page_elems:
            parsed.append(page)
        elements.extend(page_elems)

    log.info(
        "parse_scanned.done",
        source_file=pdf_path.name,
        pages=len(pages),
        pages_with_text=len(parsed),
        elements=len(elements),
    )
    return ScannedParseResult(source_file=pdf_path.name, pages_parsed=parsed, elements=elements)
