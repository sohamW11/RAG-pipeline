"""Build the memory layer (entities + affinity + metrics) onto an existing
knowledge.db. Run AFTER build.py.

    python build_memory.py [--linkpred]
"""

from __future__ import annotations

import argparse
from datetime import datetime

import kb
import memory
import threads


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--linkpred", action="store_true", help="run the link-prediction benchmark after building")
    args = ap.parse_args()

    con = kb.connect()
    print("building memory layer …")
    t0 = datetime.now()
    con.executescript(memory.SCHEMA_MEM)

    n_ent = memory.build_entities(con)
    n_de = con.execute("SELECT COUNT(*) FROM doc_entities").fetchone()[0]
    print(f"  entities          {n_ent:,} distinct · {n_de:,} doc-entity links")

    aff = memory.build_affinity(con)
    print("  affinity edges    " + ", ".join(f"{s}:{c}" for s, c in aff.items()))

    memory.build_metrics(con)
    ncomm = con.execute("SELECT COUNT(DISTINCT community) FROM doc_metrics").fetchone()[0]
    print(f"  communities       {ncomm} · PageRank + degree stored")

    th = threads.build_threads(con)
    print(f"  version threads   {th['threads']} threads over {th['threaded_docs']} docs "
          f"(+{th['inferred_edges']} inferred edges, largest {th['largest']})")
    print(f"  built in {(datetime.now()-t0).total_seconds():.1f}s")

    print("\n  top documents by PageRank (the corpus's hubs):")
    for r in con.execute("""SELECT m.doc_id, m.pagerank, d.title FROM doc_metrics m
                            JOIN documents d ON d.doc_id=m.doc_id ORDER BY m.pagerank DESC LIMIT 6"""):
        print(f"    {r['pagerank']:.4f}  {r['doc_id']}  {(r['title'] or '')[:46]}")

    if args.linkpred:
        print("\n  === link-prediction: do correlation signals predict citations? ===")
        lp = memory.link_prediction(con, k=10, n=300)
        print(f"    (n={lp['n']} seed docs, Recall@10 / MRR)")
        for s in ["co_citation", "coupling", "entity", "fused"]:
            print(f"    {s:<12} recall@10 {lp[s]['recall@k']:.3f}   mrr {lp[s]['mrr']:.3f}")
    con.close()


if __name__ == "__main__":
    main()
