"""Slice 4 — semantic embeddings over the chunks in knowledge.db.

Uses model2vec static embeddings (potion-base-8M, 256-dim): no torch / no GPU,
pure numpy at query time. Vectors are L2-normalised and stored in a numpy file
(brute-force cosine is <10 ms over ~60k×256), so there's no vector-DB / loadable-
extension dependency.

    python embed.py            # build embeddings.npy + chunk_ids.json
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import kb

HERE = Path(__file__).resolve().parent
EMB_PATH = HERE / "embeddings.npy"
IDS_PATH = HERE / "chunk_ids.json"
MODEL_NAME = "minishlab/potion-base-8M"


def build() -> None:
    from model2vec import StaticModel
    con = kb.connect()
    rows = con.execute("SELECT chunk_id, text FROM chunks ORDER BY rowid").fetchall()
    con.close()
    ids = [r["chunk_id"] for r in rows]
    texts = [r["text"] for r in rows]
    print(f"embedding {len(texts):,} chunks with {MODEL_NAME} …")
    model = StaticModel.from_pretrained(MODEL_NAME)
    emb = model.encode(texts, show_progress_bar=True).astype("float32")
    # L2-normalise so cosine == dot product
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    emb = emb / np.clip(norms, 1e-9, None)
    np.save(EMB_PATH, emb)
    IDS_PATH.write_text(json.dumps(ids))
    print(f"saved {emb.shape} -> {EMB_PATH.name} + {IDS_PATH.name}")


if __name__ == "__main__":
    build()
