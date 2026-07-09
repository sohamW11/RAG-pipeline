"""Checkpoint 0 recon: inspect Docling's element + table output on a native page.

Runs Docling on one PDF and reports:
- the sequence of elements it emits (type, page, bbox availability, text preview),
- for each detected table: #rows x #cols, an empty-cell ratio, ragged-row check,
  and a rendered grid preview — so we can see what a GOOD vs BROKEN table looks
  like in Docling's own output before we build the quality gate (Checkpoint 2).

Run: ./.venv/bin/python scripts/recon_docling.py <path-to.pdf>
"""

from __future__ import annotations

import sys
from pathlib import Path

from docling.document_converter import DocumentConverter


def table_to_grid(table_df) -> list[list[str]]:
    # Docling TableItem exposes .export_to_dataframe()
    return [[("" if c is None else str(c)).strip() for c in row] for row in table_df.values.tolist()]


def describe_table(grid: list[list[str]]) -> str:
    rows = len(grid)
    cols = max((len(r) for r in grid), default=0)
    cells = [c for r in grid for c in r]
    empty = sum(1 for c in cells if not c)
    ragged = len({len(r) for r in grid}) > 1
    empty_ratio = empty / len(cells) if cells else 1.0
    lens = [len(c) for c in cells]
    top_share = (max(lens) / sum(lens)) if sum(lens) else 0.0
    return (
        f"{rows}x{cols}  empty={empty_ratio:.0%}  ragged={ragged}  "
        f"max_single_cell_text_share={top_share:.0%}"
    )


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: recon_docling.py <path-to.pdf>")
        raise SystemExit(2)
    pdf = Path(sys.argv[1])
    print(f"Converting {pdf.name} with Docling ...\n")

    conv = DocumentConverter()
    result = conv.convert(str(pdf))
    doc = result.document

    # --- element stream ---
    print("=== element stream (texts) ===")
    for i, item in enumerate(doc.texts[:40]):
        prov = item.prov[0] if item.prov else None
        page = prov.page_no if prov else "?"
        bbox = "bbox" if (prov and prov.bbox) else "NO-BBOX"
        label = getattr(item, "label", "?")
        text = (item.text or "").replace("\n", " ")[:70]
        print(f"[{i:>3}] p{page} {str(label):<14} {bbox:<8} {text!r}")
    if len(doc.texts) > 40:
        print(f"   ... {len(doc.texts) - 40} more text items")

    # --- tables ---
    print(f"\n=== tables: {len(doc.tables)} ===")
    for ti, table in enumerate(doc.tables):
        prov = table.prov[0] if table.prov else None
        page = prov.page_no if prov else "?"
        try:
            df = table.export_to_dataframe()
            grid = table_to_grid(df)
        except Exception as exc:  # noqa: BLE001
            print(f"  table[{ti}] p{page}: export failed: {exc}")
            continue
        print(f"\n  table[{ti}] p{page}: {describe_table(grid)}")
        for r in grid[:6]:
            print("    | " + " | ".join(c[:18] for c in r))
        if len(grid) > 6:
            print(f"    ... {len(grid) - 6} more rows")


if __name__ == "__main__":
    main()
