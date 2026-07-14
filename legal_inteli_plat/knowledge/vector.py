"""Semantic vector search over the model2vec chunk embeddings (slice 4).

Brute-force cosine in numpy — no vector DB. Loads embeddings.npy once; each query
embeds the text and dots against the matrix (<10 ms for ~60k×256).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
EMB_PATH = HERE / "embeddings.npy"
IDS_PATH = HERE / "chunk_ids.json"
MODEL_NAME = "minishlab/potion-base-8M"


def available() -> bool:
    return EMB_PATH.exists() and IDS_PATH.exists()


class VectorIndex:
    def __init__(self):
        self.emb = np.load(EMB_PATH)                       # (n, 256), L2-normalised
        self.ids = json.loads(IDS_PATH.read_text())
        from model2vec import StaticModel
        self.model = StaticModel.from_pretrained(MODEL_NAME)

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        q = self.model.encode([query]).astype("float32")[0]
        q = q / (np.linalg.norm(q) + 1e-9)
        sims = self.emb @ q
        k = min(k, len(sims))
        idx = np.argpartition(-sims, k - 1)[:k]
        idx = idx[np.argsort(-sims[idx])]
        return [(self.ids[i], float(sims[i])) for i in idx]


def rrf(rank_lists: list[list[str]], K: int = 60) -> list[str]:
    """Reciprocal-rank fusion of several ranked id lists."""
    score: dict[str, float] = {}
    for rl in rank_lists:
        for rank, item in enumerate(rl):
            score[item] = score.get(item, 0.0) + 1.0 / (K + rank + 1)
    return [d for d, _ in sorted(score.items(), key=lambda x: -x[1])]
