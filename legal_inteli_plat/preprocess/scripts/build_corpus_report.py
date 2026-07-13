"""Aggregate shard progress into a unified manifest + a human-readable report.

Reads ``parsed/_progress_shard_*.jsonl`` (one record per processed doc) and the
``parsed/*.json`` files on disk, and writes:
  - ``parsed/preprocess_manifest.json`` — unified run manifest + coverage stats
  - ``parsed/CORPUS_REPORT.md``          — the "memory base" summary

Safe to run any time — partway through the batch or after it finishes.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "parsed"
CORPUS_TOTAL = 3502


def load_progress() -> dict[str, dict]:
    records: dict[str, dict] = {}
    for jl in sorted(OUT.glob("_progress_shard_*.jsonl")):
        for line in jl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            records[rec["doc_id"]] = rec  # last write wins (resume-safe)
    return records


def main() -> None:
    records = load_progress()
    on_disk = {p.stem for p in OUT.glob("*.json") if p.name != "preprocess_manifest.json"}

    ok = [r for r in records.values() if r["status"] in ("ok", "ok_with_errors")]
    failed = [r for r in records.values() if r["status"] == "failed"]

    def s(key: str) -> int:
        return sum(r.get(key, 0) for r in ok)

    native_only = [r for r in ok if r.get("native_pages", 0) and not r.get("scanned_pages", 0)]
    scanned_only = [r for r in ok if r.get("scanned_pages", 0) and not r.get("native_pages", 0)]
    mixed = [r for r in ok if r.get("native_pages", 0) and r.get("scanned_pages", 0)]
    meta_matched = sum(1 for r in ok if r.get("metadata_matched"))

    manifest = {
        "generated_at": datetime.now().isoformat(),
        "corpus_total": CORPUS_TOTAL,
        "on_disk": len(on_disk),
        "progress_records": len(records),
        "ok": len(ok),
        "with_errors": sum(1 for r in ok if r["status"] == "ok_with_errors"),
        "failed": len(failed),
        "remaining": CORPUS_TOTAL - len(on_disk),
        "pages_native": s("native_pages"),
        "pages_scanned": s("scanned_pages"),
        "elements": s("element_count"),
        "tables_found": s("tables_found"),
        "tables_repaired": s("tables_repaired"),
        "docs_native_only": len(native_only),
        "docs_scanned_only": len(scanned_only),
        "docs_mixed": len(mixed),
        "metadata_matched": meta_matched,
        "failures": [{"doc_id": r["doc_id"], "error": r.get("error", "")} for r in failed][:100],
    }
    (OUT / "preprocess_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    pct = 100 * len(on_disk) / CORPUS_TOTAL
    err_kinds = Counter(
        (r.get("error", "") or "").split(":")[0].split("(")[0].strip()[:60] for r in failed
    )
    md = f"""# SEBI Corpus Preprocessing — Coverage Report

_Generated {manifest['generated_at']}_

## Progress
- **{len(on_disk)} / {CORPUS_TOTAL} docs parsed** ({pct:.1f}%) — {manifest['remaining']} remaining
- OK: {manifest['ok']}  (clean: {manifest['ok'] - manifest['with_errors']}, with errors: {manifest['with_errors']})
- Failed: {manifest['failed']}

## Content extracted (from completed docs)
- Native pages: **{manifest['pages_native']:,}**   |   Scanned pages: **{manifest['pages_scanned']:,}**
- Elements: **{manifest['elements']:,}**
- Tables found: **{manifest['tables_found']:,}**   |   repaired by Camelot: **{manifest['tables_repaired']:,}**

## Document mix
- Native-only (Docling): {manifest['docs_native_only']}
- Scanned-only (OCR): {manifest['docs_scanned_only']}
- Mixed native+scanned (Docling + OCR): {manifest['docs_mixed']}
- Metadata joined from crawler.db: {manifest['metadata_matched']} / {manifest['ok']}

## Failures by kind
"""
    for kind, n in err_kinds.most_common(15):
        md += f"- {kind or '(unknown)'}: {n}\n"
    if not failed:
        md += "- none yet\n"
    (OUT / "CORPUS_REPORT.md").write_text(md, encoding="utf-8")
    print(f"on_disk={len(on_disk)}/{CORPUS_TOTAL} ok={len(ok)} failed={len(failed)} "
          f"tables={manifest['tables_found']} repaired={manifest['tables_repaired']}")


if __name__ == "__main__":
    main()
