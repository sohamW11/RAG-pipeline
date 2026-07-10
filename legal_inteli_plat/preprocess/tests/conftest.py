"""Shared fixtures — real SEBI PDFs (no network; runs offline in CI)."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


# Individual fixture PDFs, keyed by character (see tests/fixtures/README.txt).
@pytest.fixture(scope="session")
def native_text_pdf() -> Path:
    return FIXTURES / "native_text_13565.pdf"  # 1 page, native, no tables


@pytest.fixture(scope="session")
def native_table_pdf() -> Path:
    return FIXTURES / "native_table_83899.pdf"  # 3 pages, native, real ruled table


@pytest.fixture(scope="session")
def native_no_real_table_pdf() -> Path:
    return FIXTURES / "native_table_34658.pdf"  # 4 pages, tabular-looking, 0 real tables


@pytest.fixture(scope="session")
def scanned_pdf() -> Path:
    return FIXTURES / "scanned_25769.pdf"  # 3 pages, fully scanned (image-only)


@pytest.fixture(scope="session")
def mixed_pdf() -> Path:
    return FIXTURES / "mixed_101817.pdf"  # 5 pages: 4 native + 1 scanned (index 3)
