"""Checkpoint 0 recon: characterize native vs scanned via PyMuPDF text length.

Scans a sample of real SEBI PDFs and, for each, reports per-page extractable
character counts so we can pick a clean `native_char_threshold`. Also surfaces
mixed-page PDFs (native + scanned in the same file) and any zero-text pages.

Run: ./.venv/bin/python scripts/recon_triage.py
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

import fitz  # PyMuPDF

STORAGE = Path(__file__).resolve().parents[2] / "storage-data" / "sebi"


def page_char_counts(pdf: Path) -> list[int]:
    counts: list[int] = []
    with fitz.open(pdf) as doc:
        for page in doc:
            counts.append(len(page.get_text("text").strip()))
    return counts


def sample_pdfs(per_category: int = 3) -> list[Path]:
    picks: list[Path] = []
    for cat in sorted(STORAGE.iterdir()):
        if not cat.is_dir():
            continue
        pdfs = sorted(cat.glob("*.pdf"))[:per_category]
        picks.extend(pdfs)
    return picks


def classify(counts: list[int], threshold: int) -> str:
    if not counts:
        return "EMPTY"
    native = sum(c >= threshold for c in counts)
    if native == len(counts):
        return "all-native"
    if native == 0:
        return "all-scanned"
    return "MIXED"


def main() -> None:
    threshold = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    pdfs = sample_pdfs()
    print(f"Sampled {len(pdfs)} PDFs; threshold={threshold} chars/page\n")
    print(f"{'category/file':<70} {'pages':>5} {'min':>6} {'med':>7} {'max':>7}  class")
    mixed, scanned, empty = [], [], []
    for pdf in pdfs:
        try:
            counts = page_char_counts(pdf)
        except Exception as exc:  # noqa: BLE001 - recon script
            print(f"{pdf.name[:68]:<70}  ERROR: {exc}")
            continue
        label = classify(counts, threshold)
        rel = f"{pdf.parent.name}/{pdf.name}"[:69]
        med = int(statistics.median(counts)) if counts else 0
        print(
            f"{rel:<70} {len(counts):>5} {min(counts, default=0):>6} "
            f"{med:>7} {max(counts, default=0):>7}  {label}"
        )
        if label == "MIXED":
            mixed.append((rel, counts))
        elif label == "all-scanned":
            scanned.append(rel)
        elif label == "EMPTY":
            empty.append(rel)

    print("\n--- summary ---")
    print(f"MIXED (native+scanned in one file): {len(mixed)}")
    for rel, counts in mixed:
        lows = [i for i, c in enumerate(counts) if c < threshold]
        print(f"    {rel}  scanned_pages={lows[:10]}{'...' if len(lows) > 10 else ''}")
    print(f"all-scanned: {len(scanned)}")
    for rel in scanned:
        print(f"    {rel}")
    print(f"EMPTY (0 pages / unreadable): {len(empty)}")


if __name__ == "__main__":
    main()
