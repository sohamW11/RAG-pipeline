"""Backfill ``pdf_url`` (direct, downloadable PDF) into existing parsed JSONs.

The crawler already resolved SEBI's iframe viewer to the real PDF and stored it
in ``crawler.db`` (``documents.pdf_url``). Older parsed files were written before
preprocess carried that field forward, so they only have ``source_url`` (the HTML
landing page). This joins each parsed doc's ``doc_id`` -> ``documents.pdf_url`` and
inserts ``pdf_url`` right after ``source_url``, rewriting the file in place.

Usage:
    python -m scripts.backfill_pdf_url [--db PATH] [--parsed-dir DIR] [--dry-run]
Run from the ``preprocess/`` package root (same place as run_corpus.py).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def load_pdf_urls(db_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT document_number, pdf_url FROM documents "
            "WHERE document_number IS NOT NULL AND pdf_url IS NOT NULL AND pdf_url != ''"
        )
        return {str(r["document_number"]): r["pdf_url"] for r in rows}
    finally:
        conn.close()


def _reorder(doc: dict, pdf_url: str) -> dict:
    """Return a new dict with pdf_url inserted right after source_url."""
    out: dict = {}
    for key, val in doc.items():
        out[key] = val
        if key == "source_url":
            out["pdf_url"] = pdf_url
    if "pdf_url" not in out:  # no source_url key -> append
        out["pdf_url"] = pdf_url
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="../crawler.db", type=Path)
    ap.add_argument("--parsed-dir", default="parsed", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pdf_urls = load_pdf_urls(args.db)
    print(f"loaded {len(pdf_urls)} pdf_urls from {args.db}")

    files = sorted(args.parsed_dir.glob("*.json"))
    updated = missing = already = 0
    for f in files:
        doc = json.loads(f.read_text())
        doc_id = str(doc.get("doc_id", f.stem))
        pdf_url = pdf_urls.get(doc_id)
        if not pdf_url:
            missing += 1
            continue
        if doc.get("pdf_url") == pdf_url:
            already += 1
            continue
        new_doc = _reorder(doc, pdf_url)
        if not args.dry_run:
            f.write_text(json.dumps(new_doc, ensure_ascii=False, indent=2))
        updated += 1

    print(
        f"files={len(files)} updated={updated} already_current={already} "
        f"no_pdf_url_in_db={missing}" + (" (dry-run)" if args.dry_run else "")
    )


if __name__ == "__main__":
    main()
