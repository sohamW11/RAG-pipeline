"""Per-document orchestration of steps 1..6 (CLAUDE.md §2).

For each PDF: triage per page -> Docling on native pages -> table gate + Camelot
repair -> normalize into ``DocumentElement[]`` -> validate and write
``parsed/{doc_id}.json``. A batch writes ``preprocess_manifest.json`` with
coverage stats. Idempotent/resumable (skips docs already parsed unless ``force``);
per-page and per-document errors are isolated so one bad PDF never aborts the run.

Scanned pages are detected and recorded here, but their OCR extraction is the
Checkpoint-4 job — until then a scanned page is logged to ``errors[]`` as
"OCR deferred", never silently dropped.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import structlog

from .config import PreprocessSettings, get_settings
from .inventory import Inventory, load_inventory
from .models import DocumentElement, PageInfo, ParsedDocument
from .normalize import build_element
from .parse_native import parse_native
from .parse_scanned import parse_scanned
from .tables import evaluate_table, repair_native_table
from .triage import triage_document

log = structlog.get_logger(__name__)

# Trailing number in the crawler's filenames is the doc_id; an optional "-N"
# suffix is a crawler version tag, not a part index (parts are assigned by
# grouping files that share a doc_id).
_DOC_ID_RE = re.compile(r"_(\d+)(?:-\d+)?$")


def doc_id_from_path(path: Path) -> str:
    """Extract the crawler doc_id from a filename, or fall back to the stem."""
    match = _DOC_ID_RE.search(path.stem)
    return match.group(1) if match else path.stem


def discover_pdfs(path: Path) -> list[Path]:
    """A single PDF, or every ``*.pdf`` under a directory (recursive), sorted."""
    if path.is_file():
        return [path] if path.suffix.lower() == ".pdf" else []
    return sorted(p for p in path.rglob("*.pdf"))


def group_by_doc_id(paths: list[Path]) -> dict[str, list[Path]]:
    """Group files under one logical document; multi-file docs get part indices."""
    groups: dict[str, list[Path]] = defaultdict(list)
    for p in paths:
        groups[doc_id_from_path(p)].append(p)
    return {doc_id: sorted(files) for doc_id, files in groups.items()}


def process_document(
    doc_id: str,
    files: list[Path],
    settings: PreprocessSettings | None = None,
    inventory: Inventory | None = None,
) -> ParsedDocument:
    """Parse every file of one logical document into a validated ``ParsedDocument``."""
    settings = settings or get_settings()
    started = time.perf_counter()

    elements: list[DocumentElement] = []
    pages: list[PageInfo] = []
    errors: list[str] = []
    source_files: list[str] = []
    tables_found = tables_repaired = 0

    for part, file in enumerate(files):
        source_files.append(file.name)
        try:
            triage = triage_document(file, settings)
        except Exception as exc:  # noqa: BLE001 - isolate a bad file
            errors.append(f"{file.name}: triage failed: {exc}")
            log.warning("pipeline.triage_failed", source_file=file.name, error=str(exc))
            continue

        native = triage.native_pages()
        raw_by_page: dict[int, list] = defaultdict(list)
        if native:
            try:
                parsed = parse_native(file, native_pages=native, settings=settings)
                for el in parsed.elements:
                    raw_by_page[el.page].append(el)
            except Exception as exc:  # noqa: BLE001 - isolate a bad file
                errors.append(f"{file.name}: docling parse failed: {exc}")
                log.warning("pipeline.docling_failed", source_file=file.name, error=str(exc))

        scanned = triage.scanned_pages()
        scanned_by_page: dict[int, list] = defaultdict(list)
        if scanned:
            try:
                ocr = parse_scanned(file, scanned, settings=settings)
                for el in ocr.elements:
                    scanned_by_page[el.page].append(el)
            except Exception as exc:  # noqa: BLE001 - isolate a bad file
                errors.append(f"{file.name}: ocr failed: {exc}")
                log.warning("pipeline.ocr_failed", source_file=file.name, error=str(exc))

        for tp in triage.pages:
            page_found = page_repaired = 0
            page_elements = 0
            if tp.page_type == "scanned":
                raws = sorted(scanned_by_page.get(tp.page, []), key=lambda r: r.reading_order)
                for raw in raws:
                    elements.append(
                        build_element(raw=raw, doc_id=doc_id, part=part, source_file=file.name)
                    )
                    page_elements += 1
                if not raws:
                    errors.append(f"{file.name} p{tp.page}: scanned page produced no OCR text")
            else:
                for raw in sorted(raw_by_page.get(tp.page, []), key=lambda r: r.reading_order):
                    source_parser = notes = None
                    table_grid = None
                    if raw.label == "table":
                        page_found += 1
                        grid = raw.table or []
                        gate = evaluate_table(grid, settings)
                        if gate.is_broken and settings.parsers.camelot.enabled:
                            outcome = repair_native_table(
                                file,
                                tp.page,
                                grid,
                                settings=settings,
                                target_bbox=(raw.bbox.x0, raw.bbox.y0, raw.bbox.x1, raw.bbox.y1),
                                page_height=tp.height,
                            )
                            table_grid = outcome.grid
                            source_parser = outcome.source_parser
                            notes = outcome.note
                            if outcome.repaired:
                                page_repaired += 1
                        elif gate.is_broken:
                            notes = f"table broken ({'; '.join(gate.reasons)}); camelot disabled"
                    elements.append(
                        build_element(
                            raw=raw,
                            doc_id=doc_id,
                            part=part,
                            source_file=file.name,
                            table_grid=table_grid,
                            source_parser=source_parser,
                            notes=notes,
                        )
                    )
                    page_elements += 1

            pages.append(
                PageInfo(
                    part=part,
                    page=tp.page,
                    page_type=tp.page_type,
                    char_count=tp.char_count,
                    element_count=page_elements,
                    tables_found=page_found,
                    tables_repaired=page_repaired,
                )
            )
            tables_found += page_found
            tables_repaired += page_repaired

    record = inventory.lookup(files[0].name, doc_id) if inventory else None
    native_pages = sum(1 for p in pages if p.page_type == "native")
    scanned_pages = sum(1 for p in pages if p.page_type == "scanned")

    return ParsedDocument(
        doc_id=doc_id,
        title=record.title if record else None,
        date=record.date if record else None,
        subsection=record.subsection if record else None,
        source_url=record.source_url if record else None,
        metadata_matched=record is not None,
        source_files=source_files,
        page_count=len(pages),
        pages=pages,
        elements=elements,
        stats={
            "native_pages": native_pages,
            "scanned_pages": scanned_pages,
            "element_count": len(elements),
            "tables_found": tables_found,
            "tables_repaired": tables_repaired,
            "elapsed_sec": round(time.perf_counter() - started, 2),
        },
        errors=errors,
        parsed_at=datetime.now(),
    )


def preprocess_path(
    path: str | Path,
    settings: PreprocessSettings | None = None,
    *,
    force: bool = False,
    limit: int = 0,
    out_dir: str | Path | None = None,
) -> dict:
    """Parse a PDF or a directory of PDFs; write per-doc JSON + a run manifest.

    Resumable: skips docs already present in the output dir unless ``force``.
    ``limit`` caps how many *new* documents are processed (0 = all).
    """
    settings = settings or get_settings()
    out = Path(out_dir or settings.paths.parsed_dir)
    out.mkdir(parents=True, exist_ok=True)
    inventory = load_inventory(settings)

    groups = group_by_doc_id(discover_pdfs(Path(path)))
    manifest_docs: list[dict] = []
    processed = 0
    log.info("pipeline.start", input=str(path), docs=len(groups), out=str(out))

    for doc_id, files in sorted(groups.items()):
        target = out / f"{doc_id}.json"
        if target.exists() and not force:
            manifest_docs.append({"doc_id": doc_id, "status": "skipped"})
            continue
        if limit and processed >= limit:
            break
        processed += 1
        try:
            parsed = process_document(doc_id, files, settings, inventory)
            target.write_text(parsed.model_dump_json(indent=2), encoding="utf-8")
            manifest_docs.append(
                {
                    "doc_id": doc_id,
                    "status": "ok_with_errors" if parsed.errors else "ok",
                    "source_files": parsed.source_files,
                    "metadata_matched": parsed.metadata_matched,
                    **parsed.stats,
                    "error_count": len(parsed.errors),
                }
            )
        except Exception as exc:  # noqa: BLE001 - one bad doc never aborts the batch
            log.error("pipeline.doc_failed", doc_id=doc_id, error=str(exc))
            manifest_docs.append({"doc_id": doc_id, "status": "failed", "error": str(exc)})

    ok = [d for d in manifest_docs if d["status"] in ("ok", "ok_with_errors")]
    manifest = {
        "input": str(path),
        "generated_at": datetime.now().isoformat(),
        "documents_total": len(groups),
        "documents_processed": len(ok),
        "documents_skipped": sum(1 for d in manifest_docs if d["status"] == "skipped"),
        "documents_failed": sum(1 for d in manifest_docs if d["status"] == "failed"),
        "native_pages": sum(d.get("native_pages", 0) for d in ok),
        "scanned_pages": sum(d.get("scanned_pages", 0) for d in ok),
        "tables_found": sum(d.get("tables_found", 0) for d in ok),
        "tables_repaired": sum(d.get("tables_repaired", 0) for d in ok),
        "elements": sum(d.get("element_count", 0) for d in ok),
        "documents": manifest_docs,
    }
    (out / "preprocess_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    log.info(
        "pipeline.done",
        processed=manifest["documents_processed"],
        skipped=manifest["documents_skipped"],
        failed=manifest["documents_failed"],
    )
    return manifest
