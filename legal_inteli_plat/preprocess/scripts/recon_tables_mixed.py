"""Checkpoint 0 recon: find mixed-page PDFs and table-rich native PDFs.

- Mixed-page: a single file with both native (text) and scanned (image) pages.
- Table-rich: uses PyMuPDF's find_tables() as a cheap detector to rank native
  PDFs by how many tables they contain, so we can pick a solid native+table
  fixture for the Docling / Camelot recon.

Run: ./.venv/bin/python scripts/recon_tables_mixed.py [sample_per_cat]
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz

STORAGE = Path(__file__).resolve().parents[2] / "storage-data" / "sebi"
THRESHOLD = 100


def main() -> None:
    per_cat = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    pdfs: list[Path] = []
    for cat in sorted(STORAGE.iterdir()):
        if cat.is_dir():
            pdfs.extend(sorted(cat.glob("*.pdf"))[:per_cat])

    mixed: list[tuple[str, list[int], int]] = []
    table_rich: list[tuple[int, str, int, list[int]]] = []
    scanned: list[str] = []

    for pdf in pdfs:
        rel = f"{pdf.parent.name}/{pdf.name}"
        try:
            with fitz.open(pdf) as doc:
                char_counts = [len(p.get_text("text").strip()) for p in doc]
                native_pages = [i for i, c in enumerate(char_counts) if c >= THRESHOLD]
                n_native = len(native_pages)
                n_scanned = len(char_counts) - n_native
                # Count tables only on native pages (find_tables needs a text/vector layer).
                tbl_pages: list[int] = []
                total_tables = 0
                if n_native:
                    for i in native_pages:
                        tabs = doc[i].find_tables()
                        if tabs.tables:
                            tbl_pages.append(i)
                            total_tables += len(tabs.tables)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR {rel}: {exc}")
            continue

        if n_native and n_scanned:
            mixed.append((rel, char_counts, n_scanned))
        elif n_native == 0:
            scanned.append(rel)
        if total_tables:
            table_rich.append((total_tables, rel, len(char_counts), tbl_pages))

    print(f"Scanned {len(pdfs)} PDFs\n")
    print(f"=== MIXED-page PDFs: {len(mixed)} ===")
    for rel, counts, ns in mixed:
        low = [i for i, c in enumerate(counts) if c < THRESHOLD]
        print(f"  {rel}  pages={len(counts)} scanned={ns} scanned_idx={low[:12]}")

    print(f"\n=== fully-SCANNED PDFs: {len(scanned)} ===")
    for rel in scanned[:15]:
        print(f"  {rel}")

    print(f"\n=== TABLE-RICH native PDFs (top 15 by table count) ===")
    for total, rel, pages, tbl_pages in sorted(table_rich, reverse=True)[:15]:
        print(f"  tables={total:>3} pages={pages:>3} tbl_pages={tbl_pages[:8]}  {rel}")


if __name__ == "__main__":
    main()
