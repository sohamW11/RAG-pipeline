"""PyMuPDF triage — the scout (CLAUDE.md §2, Tool roles).

Only two jobs: decide native vs scanned **per page**, and render scanned pages
to images for the OCR / VLM path. It does NOT extract content for the native
path — that is Docling's job (see :mod:`parse_native`).

Triage rule (Checkpoint 0, verified empirically — see README recon notes): a page
with at least ``triage.native_char_threshold`` extractable text characters is
``native``; below it the page is ``scanned``. The split is clean and wide across
the corpus (scanned 0–71 chars, native 186+), and it runs per page because a
single PDF can mix native and scanned pages (e.g. the AIF master circular's
scanned page 146 inside 152 native pages).

Page numbers in the output are **1-indexed** to match Docling's ``page_no`` and
the ``PageInfo``/``DocumentElement`` schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import fitz  # PyMuPDF
import structlog
from pydantic import BaseModel

from .config import PreprocessSettings, get_settings
from .models import PageType

if TYPE_CHECKING:
    from collections.abc import Iterable

log = structlog.get_logger(__name__)


class TriagedPage(BaseModel):
    """Per-page triage verdict. Carries page geometry so downstream parsers can
    convert bboxes into a common coordinate space without reopening the PDF."""

    page: int  # 1-indexed
    page_type: PageType
    char_count: int  # extractable text chars found (stripped)
    width: float  # page width in PDF points
    height: float  # page height in PDF points


class DocumentTriage(BaseModel):
    """Triage result for one PDF file."""

    source_file: str
    page_count: int
    pages: list[TriagedPage]

    def native_pages(self) -> set[int]:
        """1-indexed page numbers classified as native (for the Docling path)."""
        return {p.page for p in self.pages if p.page_type == "native"}

    def scanned_pages(self) -> set[int]:
        """1-indexed page numbers classified as scanned (for the OCR path)."""
        return {p.page for p in self.pages if p.page_type == "scanned"}


def classify_page(char_count: int, native_char_threshold: int) -> PageType:
    """Pure classification rule: at/above the threshold is native, below scanned."""
    return "native" if char_count >= native_char_threshold else "scanned"


def triage_document(
    pdf_path: str | Path,
    settings: PreprocessSettings | None = None,
) -> DocumentTriage:
    """Classify every page of one PDF as native or scanned.

    Does not render anything — call :func:`render_scanned_page` for the scanned
    pages when the OCR path needs images. Raises on an unreadable file (the
    pipeline isolates that per-document so one bad PDF never aborts the batch).
    """
    settings = settings or get_settings()
    threshold = settings.triage.native_char_threshold
    pdf_path = Path(pdf_path)

    pages: list[TriagedPage] = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            char_count = len(page.get_text("text").strip())
            page_type = classify_page(char_count, threshold)
            pages.append(
                TriagedPage(
                    page=page.number + 1,  # PyMuPDF is 0-indexed; outputs are 1-indexed
                    page_type=page_type,
                    char_count=char_count,
                    width=page.rect.width,
                    height=page.rect.height,
                )
            )

    result = DocumentTriage(
        source_file=pdf_path.name,
        page_count=len(pages),
        pages=pages,
    )
    log.info(
        "triage.done",
        source_file=result.source_file,
        page_count=result.page_count,
        native=len(result.native_pages()),
        scanned=len(result.scanned_pages()),
    )
    return result


def render_scanned_page(
    pdf_path: str | Path,
    page: int,
    settings: PreprocessSettings | None = None,
) -> bytes:
    """Render one page to a PNG image (bytes) at the configured DPI.

    ``page`` is **1-indexed** (matching :class:`TriagedPage`). Used only for the
    scanned path; the OCR adapter (Checkpoint 4) consumes these bytes.
    """
    settings = settings or get_settings()
    dpi = settings.triage.render_dpi
    with fitz.open(pdf_path) as doc:
        pix = doc[page - 1].get_pixmap(dpi=dpi)
        return pix.tobytes("png")


def render_scanned_pages(
    pdf_path: str | Path,
    pages: Iterable[int],
    settings: PreprocessSettings | None = None,
) -> dict[int, bytes]:
    """Render several scanned pages in one open — ``{1-indexed page: PNG bytes}``."""
    settings = settings or get_settings()
    dpi = settings.triage.render_dpi
    images: dict[int, bytes] = {}
    with fitz.open(pdf_path) as doc:
        for page in pages:
            images[page] = doc[page - 1].get_pixmap(dpi=dpi).tobytes("png")
    return images
