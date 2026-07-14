"""Full metrics harness — Precision/Recall/F1, MRR, nDCG with micro & macro
averaging, per-stratum breakdown, and the score distribution.

Ground truth is bootstrapped from the corpus itself (no hand-labeling):

  Task A — KNOWN-ITEM: query = a doc's title, gold = that doc. Single-gold, so we
           report MRR / Recall@k (P/R/F1 aren't meaningful for one gold).
  Task B — RELATED-DOC: query = a doc, gold = the docs it CITES. Multi-gold, so
           the full P/R/F1 + nDCG applies. The system ranks by the UNIFIED
           relatedness with citations REMOVED (memory.relatedness use_citation=
           False), so structural/thematic signals must PREDICT the citations.

Aggregation shown three ways so "the big pile vs per-doc" is explicit:
  • micro  — pool every hit across all queries, then compute P/R/F1 once
             (large docs dominate).
  • macro  — compute P/R/F1 per query, then average (every doc counts equally).
  • distribution of per-query F1 (median / p10 / p90 / worst).
Plus a macro breakdown stratified by subsection, currency status, and era.

    python evaluate.py [--n 300] [--k 10] [--seed 0]
"""

from __future__ import annotations

import argparse
import math
import random
import statistics
from collections import defaultdict

import kb
import memory


# ------------------------------------------------------------------ helpers
def doc_rank(con, query: str, k: int) -> list[str]:
    seen: list[str] = []
    for r in kb.search(con, query, k=k * 3):
        if r["doc_id"] not in seen:
            seen.append(r["doc_id"])
        if len(seen) >= k:
            break
    return seen


def prf(retrieved: list[str], gold: set, k: int) -> dict:
    hits = [d for d in retrieved[:k] if d in gold]
    n_hits = len(hits)
    P = n_hits / k
    R = n_hits / len(gold)
    F1 = 0.0 if P + R == 0 else 2 * P * R / (P + R)
    rr = 0.0
    for i, d in enumerate(retrieved[:k]):
        if d in gold:
            rr = 1 / (i + 1)
            break
    dcg = sum(1 / math.log2(i + 2) for i, d in enumerate(retrieved[:k]) if d in gold)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(gold), k)))
    ndcg = dcg / idcg if idcg else 0.0
    return {"hits": n_hits, "k": k, "gold": len(gold), "P": P, "R": R, "F1": F1,
            "RR": rr, "nDCG": ndcg}


def era(date: str) -> str:
    y = int((date or "0")[:4] or 0)
    if y < 2013:
        return "≤2012"
    if y < 2018:
        return "2013–2017"
    return "2018+"


# ------------------------------------------------------------------ tasks
def known_item(con, sample) -> dict:
    rr = 0.0
    hit = {1: 0, 5: 0, 10: 0}
    for did, title, *_ in sample:
        ranked = doc_rank(con, title, 10)
        if did in ranked:
            r = ranked.index(did) + 1
            rr += 1 / r
            for kk in hit:
                if r <= kk:
                    hit[kk] += 1
    n = len(sample)
    return {"n": n, "MRR": rr / n, "R@1": hit[1] / n, "R@5": hit[5] / n, "R@10": hit[10] / n}


def related_doc(con, k, n, seed) -> dict:
    random.seed(seed + 7)
    have = set(d for (d,) in con.execute("SELECT doc_id FROM documents"))
    gold_of = defaultdict(set)
    for r in con.execute("SELECT src_doc, dst_doc FROM edges"):
        if r["dst_doc"] in have:
            gold_of[r["src_doc"]].add(r["dst_doc"])
    seeds = [d for d, g in gold_of.items() if g]
    seeds = random.sample(seeds, min(n, len(seeds)))

    per_query = []
    micro = {"hits": 0, "k": 0, "gold": 0}
    meta = {r["doc_id"]: r for r in
            con.execute("SELECT doc_id, subsection, status, date FROM documents")}
    for did in seeds:
        gold = gold_of[did]
        ranked = [x["doc_id"] for x in memory.relatedness(con, did, k=k, use_citation=False)]
        m = prf(ranked, gold, k)
        m["doc_id"] = did
        per_query.append(m)
        micro["hits"] += m["hits"]; micro["k"] += m["k"]; micro["gold"] += m["gold"]

    N = len(per_query)
    macro = {x: statistics.mean(q[x] for q in per_query) for x in ("P", "R", "F1", "RR", "nDCG")}
    micro_P = micro["hits"] / micro["k"]
    micro_R = micro["hits"] / micro["gold"]
    micro_F1 = 0 if micro_P + micro_R == 0 else 2 * micro_P * micro_R / (micro_P + micro_R)
    f1s = sorted(q["F1"] for q in per_query)
    dist = {"median": statistics.median(f1s),
            "p10": f1s[int(0.10 * (N - 1))], "p90": f1s[int(0.90 * (N - 1))], "worst": f1s[0]}

    # stratified macro-F1
    def strat(keyfn):
        g = defaultdict(list)
        for q in per_query:
            g[keyfn(meta[q["doc_id"]])].append(q["F1"])
        return {kk: (statistics.mean(v), len(v)) for kk, v in sorted(g.items())}

    return {"n": N, "k": k,
            "micro": {"P": micro_P, "R": micro_R, "F1": micro_F1},
            "macro": macro, "dist": dist,
            "by_subsection": strat(lambda r: r["subsection"] or "?"),
            "by_status": strat(lambda r: r["status"] or "?"),
            "by_era": strat(lambda r: era(r["date"]))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    con = kb.connect()

    random.seed(args.seed)
    docs = con.execute("SELECT doc_id, title FROM documents WHERE title IS NOT NULL AND length(title)>8").fetchall()
    sample = random.sample([(r["doc_id"], r["title"]) for r in docs], min(args.n, len(docs)))

    print("=" * 64)
    print("  METRICS HARNESS · knowledge.db")
    print("=" * 64)

    ki = known_item(con, sample)
    print(f"\n  Task A — KNOWN-ITEM (title→doc), n={ki['n']}   [single-gold]")
    print(f"    MRR {ki['MRR']:.3f}   Recall@1 {ki['R@1']:.3f}   @5 {ki['R@5']:.3f}   @10 {ki['R@10']:.3f}")

    rd = related_doc(con, args.k, args.n, args.seed)
    kk = rd["k"]
    print(f"\n  Task B — RELATED-DOC (predict a doc's citations via fused relatedness), "
          f"n={rd['n']} [multi-gold]")
    print(f"    {'':10}{'P@'+str(kk):>8}{'R@'+str(kk):>8}{'F1@'+str(kk):>8}")
    print(f"    {'micro':10}{rd['micro']['P']:>8.3f}{rd['micro']['R']:>8.3f}{rd['micro']['F1']:>8.3f}"
          "   ← the 'big pile'")
    print(f"    {'macro':10}{rd['macro']['P']:>8.3f}{rd['macro']['R']:>8.3f}{rd['macro']['F1']:>8.3f}"
          "   ← per-doc, equal weight")
    print(f"    MRR {rd['macro']['RR']:.3f}   nDCG@{kk} {rd['macro']['nDCG']:.3f}")
    d = rd["dist"]
    print(f"    per-doc F1@{kk} distribution:  median {d['median']:.3f}  "
          f"p10 {d['p10']:.3f}  p90 {d['p90']:.3f}  worst {d['worst']:.3f}")

    print(f"\n  Stratified macro-F1@{kk} (value, n):")
    for name, table in (("subsection", rd["by_subsection"]), ("status", rd["by_status"]), ("era", rd["by_era"])):
        cells = "   ".join(f"{kk2}:{v[0]:.2f}({v[1]})" for kk2, v in table.items())
        print(f"    by {name:10} {cells}")
    print("\n" + "=" * 64)
    con.close()


if __name__ == "__main__":
    main()
