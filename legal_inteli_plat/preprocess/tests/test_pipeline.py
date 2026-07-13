"""End-to-end pipeline tests against real fixtures (no inventory => hermetic)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sebi_preprocessing.config import OcrConfig, ParsersConfig, PreprocessSettings
from sebi_preprocessing.models import ParsedDocument
from sebi_preprocessing.pipeline import (
    discover_pdfs,
    doc_id_from_path,
    group_by_doc_id,
    preprocess_path,
    process_document,
)


@pytest.fixture(scope="session")
def settings() -> PreprocessSettings:
    # inventory_path=None => no crawler.db join; rapidocr => no system tesseract needed
    return PreprocessSettings(parsers=ParsersConfig(ocr=OcrConfig(adapter="rapidocr")))


# --- doc_id / discovery -------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("native_table_83899.pdf", "83899"),
        ("some-long-slug_13565.pdf", "13565"),
        ("grant-of-reward_68778-1.pdf", "68778"),  # -N version suffix, not a doc_id
        ("no-number-here.pdf", "no-number-here"),  # fallback to stem
    ],
)
def test_doc_id_from_path(name: str, expected: str) -> None:
    assert doc_id_from_path(Path(name)) == expected


def test_discover_and_group(fixtures_dir) -> None:
    pdfs = discover_pdfs(fixtures_dir)
    assert len(pdfs) == 5
    groups = group_by_doc_id(pdfs)
    assert set(groups) == {"13565", "83899", "34658", "25769", "101817"}
    assert all(len(v) == 1 for v in groups.values())  # one file per doc_id here


# --- process_document on a native+table doc -----------------------------------


@pytest.fixture(scope="session")
def parsed_bankers(native_table_pdf, settings) -> ParsedDocument:
    return process_document("83899", [native_table_pdf], settings)


def test_parsed_document_is_valid(parsed_bankers) -> None:
    assert isinstance(parsed_bankers, ParsedDocument)
    assert parsed_bankers.doc_id == "83899"
    assert parsed_bankers.page_count == 3
    assert parsed_bankers.metadata_matched is False  # no inventory supplied
    assert parsed_bankers.source_files == ["native_table_83899.pdf"]
    assert parsed_bankers.elements
    assert parsed_bankers.stats["native_pages"] == 3


def test_every_element_carries_provenance(parsed_bankers) -> None:
    for el in parsed_bankers.elements:
        assert el.doc_id == "83899"
        assert el.source_file == "native_table_83899.pdf"
        assert el.page >= 1
        assert el.bbox is not None
        assert el.element_id == f"83899:{el.part}:p{el.page}:{el.reading_order}"


def test_element_ids_unique(parsed_bankers) -> None:
    ids = [el.element_id for el in parsed_bankers.elements]
    assert len(ids) == len(set(ids))


def test_table_element_present(parsed_bankers) -> None:
    tables = [el for el in parsed_bankers.elements if el.type == "table"]
    assert len(tables) == 1
    assert tables[0].table and any("ASBA" in c for row in tables[0].table for c in row)
    assert parsed_bankers.stats["tables_found"] == 1


# --- mixed doc: scanned page recorded, not dropped ----------------------------


def test_mixed_doc_ocrs_scanned_and_docling_native(mixed_pdf, settings) -> None:
    # One doc exercising BOTH engines: Docling on the 4 native pages, OCR on p4.
    parsed = process_document("101817", [mixed_pdf], settings)
    assert parsed.page_count == 5
    scanned = [p for p in parsed.pages if p.page_type == "scanned"]
    assert len(scanned) == 1 and scanned[0].page == 4

    by_parser_page = {(el.source_parser, el.page) for el in parsed.elements}
    # native pages parsed by docling
    assert any(sp == "docling" and pg in {1, 2, 3, 5} for sp, pg in by_parser_page)
    # the scanned page parsed by ocr
    assert ("ocr", 4) in by_parser_page
    assert parsed.stats["scanned_pages"] == 1 and parsed.stats["native_pages"] == 4


# --- batch: writes JSON + manifest, is resumable ------------------------------


def test_preprocess_path_writes_and_resumes(native_text_pdf, settings, tmp_path: Path) -> None:
    out = tmp_path / "parsed"
    manifest = preprocess_path(native_text_pdf, settings, out_dir=out)
    assert manifest["documents_processed"] == 1
    doc_json = out / "13565.json"
    assert doc_json.exists()
    # written file re-validates against the schema
    ParsedDocument.model_validate_json(doc_json.read_text())
    assert (out / "preprocess_manifest.json").exists()

    # second run without --force skips the already-parsed doc
    again = preprocess_path(native_text_pdf, settings, out_dir=out)
    assert again["documents_skipped"] == 1
    assert again["documents_processed"] == 0

    # --force re-parses
    forced = preprocess_path(native_text_pdf, settings, force=True, out_dir=out)
    assert forced["documents_processed"] == 1


def test_limit_caps_processing(fixtures_dir, settings, tmp_path: Path) -> None:
    out = tmp_path / "parsed"
    manifest = preprocess_path(fixtures_dir, settings, limit=1, out_dir=out)
    assert manifest["documents_processed"] == 1
    written = list(out.glob("*.json"))
    # exactly one doc JSON + the manifest
    assert len([p for p in written if p.name != "preprocess_manifest.json"]) == 1
