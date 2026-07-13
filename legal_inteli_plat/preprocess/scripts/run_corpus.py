"""Sharded, resumable full-corpus batch runner.

Processes every ``doc_id % nshards == shard`` document from the SEBI corpus into
``parsed/{doc_id}.json`` (skipping any already present), and appends one JSON line
per doc to ``parsed/_progress_shard_{shard}.jsonl`` so progress survives crashes
and can be monitored live. Launch one process per shard in parallel:

    for i in 0 1 2 3; do
      OMP_NUM_THREADS=3 MKL_NUM_THREADS=3 \
        ./.venv/bin/python scripts/run_corpus.py --shards 4 --shard $i \
        > logs/corpus_shard_$i.log 2>&1 &
    done

Idempotent: re-running skips finished docs, so a killed run just resumes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Make the package importable no matter how the script is launched.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch

from sebi_preprocessing.config import get_settings
from sebi_preprocessing.inventory import load_inventory
from sebi_preprocessing.pipeline import discover_pdfs, group_by_doc_id, process_document

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT.parent / "storage-data" / "sebi"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--out", type=str, default="parsed")
    args = ap.parse_args()

    torch.set_num_threads(args.threads)
    settings = get_settings()
    inventory = load_inventory(settings)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    progress = out / f"_progress_shard_{args.shard}.jsonl"

    groups = group_by_doc_id(discover_pdfs(CORPUS))
    doc_ids = sorted(groups)
    mine = [d for i, d in enumerate(doc_ids) if i % args.shards == args.shard]
    print(f"[shard {args.shard}/{args.shards}] {len(mine)} of {len(doc_ids)} docs", flush=True)

    done = skipped = failed = 0
    with progress.open("a", encoding="utf-8") as plog:
        for n, doc_id in enumerate(mine, 1):
            target = out / f"{doc_id}.json"
            if target.exists():
                skipped += 1
                continue
            t0 = time.perf_counter()
            try:
                parsed = process_document(doc_id, groups[doc_id], settings, inventory)
                target.write_text(parsed.model_dump_json(indent=2), encoding="utf-8")
                rec = {
                    "doc_id": doc_id,
                    "status": "ok_with_errors" if parsed.errors else "ok",
                    "metadata_matched": parsed.metadata_matched,
                    **parsed.stats,
                    "error_count": len(parsed.errors),
                }
                done += 1
            except Exception as exc:  # noqa: BLE001 - one bad doc never aborts the shard
                rec = {"doc_id": doc_id, "status": "failed", "error": str(exc)}
                failed += 1
            plog.write(json.dumps(rec) + "\n")
            plog.flush()
            if n % 20 == 0 or (time.perf_counter() - t0) > 30:
                print(
                    f"[shard {args.shard}] {n}/{len(mine)} "
                    f"done={done} skip={skipped} fail={failed} last={doc_id} "
                    f"({time.perf_counter()-t0:.1f}s)",
                    flush=True,
                )

    print(f"[shard {args.shard}] FINISHED done={done} skipped={skipped} failed={failed}", flush=True)


if __name__ == "__main__":
    main()
