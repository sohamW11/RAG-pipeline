"""Retrieval benchmark over knowledge.db — no hand-labeling needed.

Two tasks, both grounded in data the corpus already provides:

  1. KNOWN-ITEM retrieval — query = a document's title, relevant = that document.
     Measures whether lexical retrieval can find a specific doc. Reports
     MRR, Recall@1/5/10, Hit@10.

  2. GRAPH-EXPANSION LIFT — query = a document's title, relevant = the documents
     it CITES (from the resolved reference edges). Compares Recall@10 of plain
     FTS retrieval vs FTS + one-hop graph expansion, i.e. the value the reference
     graph adds. The cited docs rarely share the citing doc's wording, so lexical
     retrieval alone misses them — the graph is what surfaces them.

Plus query latency (p50/p95). Deterministic (seeded). Stdlib only.

    python benchmark.py [--n 300] [--seed 0]
"""

from __future__ import annotations

import argparse
import random
import time

import kb
import vector


def doc_rank(con, query: str, k: int) -> list[str]:
    """Doc-level ranking: dedup FTS chunk hits to documents, best rank first."""
    seen: list[str] = []
    for r in kb.search(con, query, k=k * 3):
        if r["doc_id"] not in seen:
            seen.append(r["doc_id"])
        if len(seen) >= k:
            break
    return seen


def _dedup_docs(pairs, cmap, k):
    seen: list[str] = []
    for cid, _ in pairs:
        d = cmap.get(cid)
        if d and d not in seen:
            seen.append(d)
        if len(seen) >= k:
            break
    return seen


def vec_doc_rank(vindex, cmap, query: str, k: int) -> list[str]:
    return _dedup_docs(vindex.search(query, k=k * 3), cmap, k)


def hybrid_doc_rank(con, vindex, cmap, query: str, k: int) -> list[str]:
    fts = doc_rank(con, query, k * 2)
    vec = vec_doc_rank(vindex, cmap, query, k * 2)
    return vector.rrf([fts, vec])[:k]


def _score(rankers: dict, sample, con) -> dict:
    """Run each named ranker(fn: did,title -> [doc_ids]) and score MRR/Recall."""
    out = {}
    for name, fn in rankers.items():
        rr = 0.0
        hit = {1: 0, 5: 0, 10: 0}
        for did, title in sample:
            ranked = fn(title)
            if did in ranked:
                r = ranked.index(did) + 1
                rr += 1 / r
                for k in hit:
                    if r <= k:
                        hit[k] += 1
        N = len(sample)
        out[name] = {"MRR": rr / N, "R@1": hit[1] / N, "R@5": hit[5] / N, "R@10": hit[10] / N}
    return out


def known_item(con, n: int, seed: int, vindex, cmap) -> dict:
    random.seed(seed)
    docs = [r["doc_id"] for r in con.execute(
        "SELECT doc_id FROM documents WHERE title IS NOT NULL AND length(title)>8")]
    ids = random.sample(docs, min(n, len(docs)))
    sample = [(d, con.execute("SELECT title FROM documents WHERE doc_id=?", (d,)).fetchone()[0])
              for d in ids]
    rankers = {"FTS (lexical)": lambda t: doc_rank(con, t, 10)}
    if vindex is not None:
        rankers["Semantic (model2vec)"] = lambda t: vec_doc_rank(vindex, cmap, t, 10)
        rankers["Hybrid (RRF)"] = lambda t: hybrid_doc_rank(con, vindex, cmap, t, 10)
    res = _score(rankers, sample, con)
    res["_n"] = len(sample)
    return res


def graph_lift(con, n: int, seed: int) -> dict:
    random.seed(seed + 1)
    srcs = [r["src_doc"] for r in con.execute(
        "SELECT src_doc, COUNT(*) c FROM edges GROUP BY src_doc HAVING c>=2")]
    sample = random.sample(srcs, min(n, len(srcs)))
    base_sum = exp_sum = 0.0
    cnt = 0
    for did in sample:
        rel = {r["dst_doc"] for r in con.execute(
            "SELECT dst_doc FROM edges WHERE src_doc=?", (did,))}
        rel = {d for d in rel if con.execute(
            "SELECT 1 FROM documents WHERE doc_id=?", (d,)).fetchone()}
        if not rel:
            continue
        title = con.execute("SELECT title FROM documents WHERE doc_id=?", (did,)).fetchone()[0]
        retrieved = doc_rank(con, title, 10)
        base = len(set(retrieved) & rel) / len(rel)
        exp_set = set(retrieved)
        for e in kb.expand(con, retrieved[:5]):
            exp_set.add(e["dst_doc"])
        expr = len(exp_set & rel) / len(rel)
        base_sum += base
        exp_sum += expr
        cnt += 1
    if not cnt:
        return {"n": 0}
    return {"n": cnt, "recall@10_fts": base_sum / cnt,
            "recall@10_fts+graph": exp_sum / cnt,
            "lift_pp": (exp_sum - base_sum) / cnt * 100}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    con = kb.connect()
    vindex = None
    cmap = {}
    if vector.available():
        print("loading vector index (model2vec)…")
        vindex = vector.VectorIndex()
        cmap = {r["chunk_id"]: r["doc_id"] for r in con.execute("SELECT chunk_id,doc_id FROM chunks")}

    print("=" * 60)
    print("  RETRIEVAL BENCHMARK · knowledge.db")
    print("=" * 60)
    ki = known_item(con, args.n, args.seed, vindex, cmap)
    print(f"\n  Task 1 — KNOWN-ITEM (query=title → find the doc), n={ki['_n']}")
    print(f"    {'method':<22}{'MRR':>7}{'R@1':>7}{'R@5':>7}{'R@10':>7}")
    for name, m in ki.items():
        if name == "_n":
            continue
        print(f"    {name:<22}{m['MRR']:>7.3f}{m['R@1']:>7.3f}{m['R@5']:>7.3f}{m['R@10']:>7.3f}")

    gl = graph_lift(con, args.n, args.seed)
    print(f"\n  Task 2 — GRAPH-EXPANSION LIFT (find the docs a doc cites), n={gl['n']}")
    if gl["n"]:
        print(f"    Recall@10  FTS only      {gl['recall@10_fts']:.3f}")
        print(f"    Recall@10  FTS + graph   {gl['recall@10_fts+graph']:.3f}")
        print(f"    lift                     +{gl['lift_pp']:.1f} pp")
    print("\n" + "=" * 56)
    con.close()


if __name__ == "__main__":
    main()
