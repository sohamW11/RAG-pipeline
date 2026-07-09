"""Checkpoint 0 recon: confirm Camelot runs on a native PDF and inspect output.

Camelot is the repair specialist for broken *native* tables (it reads vector
ruling lines). This runs both flavors on given pages and reports the shape +
accuracy/whitespace reports Camelot attaches, plus a grid preview — so we can
compare its extraction against Docling's for the same table.

Run: ./.venv/bin/python scripts/recon_camelot.py <path-to.pdf> [pages]
     pages default "all" (e.g. "1,2,5-7").
"""

from __future__ import annotations

import sys
from pathlib import Path

import camelot


def preview(table) -> None:
    grid = [[str(c).strip() for c in row] for row in table.df.values.tolist()]
    rows = len(grid)
    cols = max((len(r) for r in grid), default=0)
    report = getattr(table, "parsing_report", {})
    print(
        f"  page {report.get('page')}: {rows}x{cols}  "
        f"accuracy={report.get('accuracy')}  whitespace={report.get('whitespace')}"
    )
    for r in grid[:6]:
        print("    | " + " | ".join(c[:18] for c in r))
    if len(grid) > 6:
        print(f"    ... {len(grid) - 6} more rows")


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: recon_camelot.py <path-to.pdf> [pages]")
        raise SystemExit(2)
    pdf = Path(sys.argv[1])
    pages = sys.argv[2] if len(sys.argv) > 2 else "all"

    for flavor in ("lattice", "stream"):
        print(f"\n=== camelot flavor={flavor} pages={pages} ===")
        try:
            tables = camelot.read_pdf(str(pdf), pages=pages, flavor=flavor)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            continue
        print(f"  found {tables.n} table(s)")
        for t in tables:
            preview(t)


if __name__ == "__main__":
    main()
