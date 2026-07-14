"""Memory layer over knowledge.db — correlation structure beyond direct citations.

Adds four tables and the code to build + query them (stdlib + numpy only):

  entities / doc_entities  — SEBI gazetteer + Regulation/Section mentions per doc
  affinity                 — doc↔doc edges from THREE correlation signals:
                               co_citation (cited by the same docs)
                               coupling    (citing the same docs)
                               entity      (sharing rare entities, IDF-weighted)
                             each normalised to [0,1], pruned to top-N per doc
  doc_metrics              — PageRank centrality + label-propagation community

Query helpers power the agentic layer; link_prediction() benchmarks each signal.
"""

from __future__ import annotations

import math
import random
import sqlite3
from collections import Counter, defaultdict

import numpy as np

import entities as ent

SCHEMA_MEM = """
DROP TABLE IF EXISTS entities;
DROP TABLE IF EXISTS doc_entities;
DROP TABLE IF EXISTS affinity;
DROP TABLE IF EXISTS doc_metrics;
CREATE TABLE entities(entity_id INTEGER PRIMARY KEY, kind TEXT, name TEXT, norm TEXT UNIQUE, df INTEGER DEFAULT 0);
CREATE TABLE doc_entities(doc_id TEXT, entity_id INTEGER, count INTEGER, PRIMARY KEY(doc_id, entity_id));
CREATE INDEX idx_de_ent ON doc_entities(entity_id);
CREATE INDEX idx_de_doc ON doc_entities(doc_id);
CREATE TABLE affinity(src_doc TEXT, dst_doc TEXT, signal TEXT, weight REAL, PRIMARY KEY(src_doc, dst_doc, signal));
CREATE INDEX idx_aff_src ON affinity(src_doc);
CREATE TABLE doc_metrics(doc_id TEXT PRIMARY KEY, pagerank REAL, community INTEGER, degree INTEGER);
"""

_DF_CAP = 150          # skip entities in more than this many docs (too generic)
_TOPN = 20             # keep top-N affinity neighbours per doc per signal


# ------------------------------------------------------------------ entities
def build_entities(con: sqlite3.Connection) -> int:
    ent_id: dict[str, int] = {}
    df: Counter = Counter()
    de_rows: list[tuple] = []
    for r in con.execute("SELECT doc_id, group_concat(text, ' ') t FROM chunks GROUP BY doc_id"):
        found = ent.extract(r["t"] or "")
        for norm, (kind, cnt) in found.items():
            if norm not in ent_id:
                ent_id[norm] = len(ent_id) + 1
                con.execute("INSERT INTO entities(entity_id,kind,name,norm) VALUES(?,?,?,?)",
                            (ent_id[norm], kind, ent.display_name(norm), norm))
            de_rows.append((r["doc_id"], ent_id[norm], cnt))
            df[norm] += 1
    con.executemany("INSERT OR IGNORE INTO doc_entities VALUES(?,?,?)", de_rows)
    for norm, d in df.items():
        con.execute("UPDATE entities SET df=? WHERE norm=?", (d, norm))
    con.commit()
    return len(ent_id)


# ------------------------------------------------------------------ affinity
def _store_norm(con, counter: dict, signal: str, degmap: dict) -> None:
    """Salton-cosine normalise (co-citation/coupling) then scale to [0,1]."""
    scored = {}
    for (a, b), c in counter.items():
        scored[(a, b)] = c / math.sqrt(max(degmap.get(a, 1) * degmap.get(b, 1), 1))
    if not scored:
        return
    mx = max(scored.values())
    rows = []
    for (a, b), w in scored.items():
        w /= mx
        rows.append((a, b, signal, w))
        rows.append((b, a, signal, w))
    con.executemany("INSERT OR REPLACE INTO affinity VALUES(?,?,?,?)", rows)


def build_affinity(con: sqlite3.Connection) -> dict:
    outgoing: dict[str, set] = defaultdict(set)
    incoming: dict[str, set] = defaultdict(set)
    for r in con.execute("SELECT src_doc, dst_doc FROM edges"):
        outgoing[r["src_doc"]].add(r["dst_doc"])
        incoming[r["dst_doc"]].add(r["src_doc"])

    coupling: Counter = Counter()     # share a cited doc
    cocitation: Counter = Counter()   # share a citing doc

    def pairs(members):
        m = sorted(members)
        for i in range(len(m)):
            for j in range(i + 1, len(m)):
                yield m[i], m[j]

    for citers in incoming.values():          # coupling
        for a, b in pairs(citers):
            coupling[(a, b)] += 1
    for cited in outgoing.values():            # co-citation
        for a, b in pairs(cited):
            cocitation[(a, b)] += 1

    _store_norm(con, coupling, "coupling", {d: len(s) for d, s in outgoing.items()})
    _store_norm(con, cocitation, "co_citation", {d: len(s) for d, s in incoming.items()})

    # entity overlap (IDF-weighted), skipping over-generic entities
    n_docs = con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    ent_docs: dict[int, list] = defaultdict(list)
    for r in con.execute("SELECT doc_id, entity_id FROM doc_entities"):
        ent_docs[r["entity_id"]].append(r["doc_id"])
    dfrow = {r["entity_id"]: r["df"] for r in con.execute("SELECT entity_id, df FROM entities")}
    epair: Counter = Counter()
    for eid, docs in ent_docs.items():
        d = dfrow.get(eid, len(docs))
        if d < 2 or d > _DF_CAP:
            continue
        idf = math.log(n_docs / d)
        for a, b in pairs(set(docs)):
            epair[(a, b)] += idf
    if epair:
        mx = max(epair.values())
        rows = []
        for (a, b), w in epair.items():
            rows.append((a, b, "entity", w / mx))
            rows.append((b, a, "entity", w / mx))
        con.executemany("INSERT OR REPLACE INTO affinity VALUES(?,?,?,?)", rows)

    # prune to top-N per (src, signal)
    con.execute("""DELETE FROM affinity WHERE rowid IN (
        SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER
          (PARTITION BY src_doc, signal ORDER BY weight DESC) rn FROM affinity) WHERE rn > ?)""",
                (_TOPN,))
    con.commit()
    counts = dict(con.execute("SELECT signal, COUNT(*) FROM affinity GROUP BY signal").fetchall())
    return counts


# ------------------------------------------------------------------ metrics
def build_metrics(con: sqlite3.Connection) -> None:
    docs = [r["doc_id"] for r in con.execute("SELECT doc_id FROM documents ORDER BY doc_id")]
    idx = {d: i for i, d in enumerate(docs)}
    n = len(docs)

    outlinks: dict[int, list] = defaultdict(list)
    degree = Counter()
    for r in con.execute("SELECT src_doc, dst_doc FROM edges"):
        if r["src_doc"] in idx and r["dst_doc"] in idx:
            outlinks[idx[r["src_doc"]]].append(idx[r["dst_doc"]])
            degree[r["src_doc"]] += 1
            degree[r["dst_doc"]] += 1

    # PageRank (power iteration)
    pr = np.ones(n) / max(n, 1)
    damp = 0.85
    for _ in range(60):
        new = np.full(n, (1 - damp) / max(n, 1))
        dangling = 0.0
        for i in range(n):
            outs = outlinks.get(i)
            if outs:
                s = damp * pr[i] / len(outs)
                for j in outs:
                    new[j] += s
            else:
                dangling += pr[i]
        new += damp * dangling / max(n, 1)
        if np.abs(new - pr).sum() < 1e-7:
            pr = new
            break
        pr = new

    # communities: weighted label propagation on the affinity graph (deterministic)
    neigh: dict[str, list] = defaultdict(list)
    for r in con.execute("SELECT src_doc, dst_doc, weight FROM affinity"):
        neigh[r["src_doc"]].append((r["dst_doc"], r["weight"]))
    label = {d: i for i, d in enumerate(docs)}
    for _ in range(15):
        changed = 0
        for d in docs:
            if not neigh.get(d):
                continue
            votes: dict[int, float] = defaultdict(float)
            for nb, w in neigh[d]:
                votes[label[nb]] += w
            best = max(votes.items(), key=lambda x: (x[1], -x[0]))[0]
            if label[d] != best:
                label[d] = best
                changed += 1
        if changed == 0:
            break
    # renumber communities compactly
    remap: dict[int, int] = {}
    for d in docs:
        remap.setdefault(label[d], len(remap))

    con.execute("DELETE FROM doc_metrics")
    con.executemany("INSERT INTO doc_metrics VALUES(?,?,?,?)",
                    [(d, float(pr[idx[d]]), remap[label[d]], degree.get(d, 0)) for d in docs])
    con.commit()


# ------------------------------------------------------------------ queries
def related_by_affinity(con: sqlite3.Connection, doc_ids: list[str], k: int = 8) -> list[dict]:
    if not doc_ids:
        return []
    qs = ",".join("?" * len(doc_ids))
    agg: dict[str, dict] = defaultdict(lambda: {"score": 0.0, "signals": set()})
    for r in con.execute(
        f"""SELECT dst_doc, signal, SUM(weight) w FROM affinity
            WHERE src_doc IN ({qs}) AND dst_doc NOT IN ({qs})
            GROUP BY dst_doc, signal""", (*doc_ids, *doc_ids)):
        agg[r["dst_doc"]]["score"] += r["w"]
        agg[r["dst_doc"]]["signals"].add(r["signal"])
    top = sorted(agg.items(), key=lambda x: -x[1]["score"])[:k]
    out = []
    for did, info in top:
        row = con.execute("SELECT title, subsection, status FROM documents WHERE doc_id=?", (did,)).fetchone()
        out.append({"doc_id": did, "title": row["title"] if row else None,
                    "subsection": row["subsection"] if row else None,
                    "status": row["status"] if row else "live",
                    "score": round(info["score"], 3), "signals": sorted(info["signals"])})
    return out


def concepts_for(con: sqlite3.Connection, doc_ids: list[str], top: int = 8) -> list[dict]:
    """Distinctive concepts across a set of docs (count/df favours the specific)."""
    if not doc_ids:
        return []
    qs = ",".join("?" * len(doc_ids))
    rows = con.execute(
        f"""SELECT e.name, e.kind, SUM(de.count) c, e.df FROM doc_entities de
            JOIN entities e ON e.entity_id=de.entity_id
            WHERE de.doc_id IN ({qs})
            GROUP BY e.entity_id ORDER BY SUM(de.count)*1.0/e.df DESC LIMIT ?""",
        (*doc_ids, top)).fetchall()
    return [{"name": r["name"], "kind": r["kind"]} for r in rows]


def _gnode(con: sqlite3.Connection, did: str) -> dict:
    r = con.execute(
        """SELECT d.doc_id, d.title, d.subsection, d.status,
                  m.pagerank, m.community, m.degree
           FROM documents d LEFT JOIN doc_metrics m ON m.doc_id=d.doc_id
           WHERE d.doc_id=?""", (did,)).fetchone()
    if not r:
        return {"id": did, "title": None, "subsection": None, "status": "live",
                "pagerank": 0.0, "community": -1, "degree": 0}
    return {"id": r["doc_id"], "title": r["title"], "subsection": r["subsection"],
            "status": r["status"] or "live", "pagerank": r["pagerank"] or 0.0,
            "community": r["community"] if r["community"] is not None else -1,
            "degree": r["degree"] or 0}


def subgraph(con: sqlite3.Connection, focus: str, cap: int = 38) -> dict:
    """Focus doc + its citation neighbours + supersession + top affinity neighbours."""
    ids = {focus}
    edges = []
    for r in con.execute(
        """SELECT src_doc, dst_doc, relation, confidence FROM edges
           WHERE src_doc=? OR dst_doc=? ORDER BY confidence DESC LIMIT 24""", (focus, focus)):
        ids.add(r["src_doc"]); ids.add(r["dst_doc"])
        edges.append({"src": r["src_doc"], "dst": r["dst_doc"], "kind": "citation",
                      "rel": r["relation"], "w": r["confidence"]})
    for r in con.execute(
        "SELECT dst_doc, signal, weight FROM affinity WHERE src_doc=? ORDER BY weight DESC LIMIT 12",
        (focus,)):
        ids.add(r["dst_doc"])
        edges.append({"src": focus, "dst": r["dst_doc"], "kind": "affinity",
                      "rel": r["signal"], "w": r["weight"]})
    if len(ids) > cap:
        keep = {focus} | {e["dst"] for e in edges[:cap - 1]} | {e["src"] for e in edges[:cap - 1]}
        ids = set(list(keep)[:cap])
        ids.add(focus)
    edges = [e for e in edges if e["src"] in ids and e["dst"] in ids]
    return {"focus": focus, "nodes": [_gnode(con, d) for d in ids], "edges": edges}


def tree_subgraph(con: sqlite3.Connection, focus: str, max1: int = 10, max2: int = 2) -> dict:
    """A 2-level hierarchy rooted at focus: focus → what it cites (level 1) →
    what those cite (level 2). Fills level 1 with top affinity if citations are
    thin. Each node carries depth + parent + relation so the client lays out a
    proper top-down tree (a doc becomes a child only once — a spanning tree)."""
    def cite_children(did, limit):
        # both directions: what `did` cites AND what cites `did` — so a regulation
        # that is referenced (not referencing) still yields a hierarchy.
        return [(r["nb"], r["rel"], "citation", r["c"]) for r in con.execute(
            """SELECT dst_doc nb, relation rel, confidence c FROM edges WHERE src_doc=?
               UNION SELECT src_doc, relation, confidence FROM edges WHERE dst_doc=?
               ORDER BY c DESC LIMIT ?""", (did, did, limit))]

    meta = {focus: (0, None, None)}
    order = [focus]
    edges = []

    l1 = cite_children(focus, max1)
    if len(l1) < max1:
        have = {x[0] for x in l1}
        for r in con.execute(
            "SELECT dst_doc, signal, weight FROM affinity WHERE src_doc=? ORDER BY weight DESC LIMIT ?",
            (focus, max1)):
            if r["dst_doc"] not in have and r["dst_doc"] != focus:
                l1.append((r["dst_doc"], r["signal"], "affinity", r["weight"]))
                if len(l1) >= max1:
                    break
    for did, rel, kind, w in l1:
        if did == focus or did in meta:
            continue
        meta[did] = (1, focus, rel); order.append(did)
        edges.append({"src": focus, "dst": did, "rel": rel, "kind": kind, "w": w})
    for did, _, _, _ in l1:
        for cdid, crel, ckind, cw in cite_children(did, max2):
            if cdid in meta:
                continue
            meta[cdid] = (2, did, crel); order.append(cdid)
            edges.append({"src": did, "dst": cdid, "rel": crel, "kind": ckind, "w": cw})

    nodes = []
    for did in order:
        n = _gnode(con, did)
        d, p, rel = meta[did]
        n["depth"], n["parent"], n["rel"] = d, p, rel
        nodes.append(n)
    return {"focus": focus, "mode": "tree", "nodes": nodes, "edges": edges}


def hub_graph(con: sqlite3.Connection, n: int = 28) -> dict:
    """The corpus backbone: top-PageRank docs and the citations among them."""
    top = [r["doc_id"] for r in con.execute(
        "SELECT doc_id FROM doc_metrics ORDER BY pagerank DESC LIMIT ?", (n,))]
    idset = set(top)
    if not idset:
        return {"focus": None, "nodes": [], "edges": []}
    qs = ",".join("?" * len(idset))
    edges = [{"src": r["src_doc"], "dst": r["dst_doc"], "kind": "citation",
              "rel": r["relation"], "w": r["confidence"]}
             for r in con.execute(
                 f"""SELECT src_doc, dst_doc, relation, confidence FROM edges
                     WHERE src_doc IN ({qs}) AND dst_doc IN ({qs})""", (*idset, *idset))]
    return {"focus": None, "nodes": [_gnode(con, d) for d in top], "edges": edges}


def relatedness(con: sqlite3.Connection, doc_id: str, k: int = 10,
                use_citation: bool = True) -> list[dict]:
    """UNIFIED relatedness — one ranked list fusing every signal by Reciprocal
    Rank Fusion (RRF). Each signal contributes an ordered list; a doc's score is
    Σ 1/(K+rank). RRF needs no cross-signal calibration, so citation confidence,
    affinity weight and (future) semantic cosine combine without a scale fight.

    Signals fused: citation-out, citation-in, co_citation, coupling, entity.
    `use_citation=False` drops the citation lists — used by the benchmark so the
    structural/thematic signals must PREDICT citations rather than copy them.
    """
    lists: list[list[str]] = []
    tag: dict[str, set] = defaultdict(set)

    def add(rows, name):
        seq = [r[0] for r in rows if r[0] != doc_id]
        if seq:
            lists.append(seq)
            for d in seq:
                tag[d].add(name)

    if use_citation:
        add(con.execute("SELECT dst_doc FROM edges WHERE src_doc=? ORDER BY confidence DESC", (doc_id,)), "cites")
        add(con.execute("SELECT src_doc FROM edges WHERE dst_doc=? ORDER BY confidence DESC", (doc_id,)), "cited-by")
    for sig in ("co_citation", "coupling", "entity"):
        add(con.execute("SELECT dst_doc FROM affinity WHERE src_doc=? AND signal=? "
                        "ORDER BY weight DESC LIMIT 40", (doc_id, sig)), sig)

    K = 60
    score: dict[str, float] = defaultdict(float)
    for seq in lists:
        for rank, d in enumerate(seq):
            score[d] += 1.0 / (K + rank + 1)
    ranked = sorted(score.items(), key=lambda x: -x[1])[:k]

    out = []
    for d, s in ranked:
        row = con.execute("SELECT title, subsection, status FROM documents WHERE doc_id=?", (d,)).fetchone()
        out.append({"doc_id": d, "score": round(s, 4), "signals": sorted(tag[d]),
                    "title": row["title"] if row else None,
                    "subsection": row["subsection"] if row else None,
                    "status": row["status"] if row else None})
    return out


def entities_of(con: sqlite3.Connection, doc_id: str, top: int = 8) -> list[dict]:
    rows = con.execute(
        """SELECT e.name, e.kind, e.df, de.count FROM doc_entities de
           JOIN entities e ON e.entity_id=de.entity_id WHERE de.doc_id=?
           ORDER BY (de.count * 1.0 / e.df) DESC LIMIT ?""", (doc_id, top)).fetchall()
    return [{"name": r["name"], "kind": r["kind"], "count": r["count"]} for r in rows]


# --------------------------------------------------------------- benchmark
def link_prediction(con: sqlite3.Connection, k: int = 10, n: int = 300, seed: int = 0) -> dict:
    """Do the correlation signals predict who a doc actually cites?

    For each seed doc with citations, rank related docs by each affinity signal
    (and fused), measure Recall@k / MRR at recovering the doc's real citation
    targets. The affinity signals are derived, NOT the direct citation, so this
    is a fair self-supervised test of the memory layer's predictive value.
    """
    cited: dict[str, set] = defaultdict(set)
    for r in con.execute("SELECT src_doc, dst_doc FROM edges"):
        cited[r["src_doc"]].add(r["dst_doc"])
    seeds = [d for d, s in cited.items() if s]
    random.seed(seed)
    seeds = random.sample(seeds, min(n, len(seeds)))

    def rank(src, signal):
        if signal == "fused":
            rows = con.execute(
                "SELECT dst_doc, SUM(weight) w FROM affinity WHERE src_doc=? GROUP BY dst_doc ORDER BY w DESC LIMIT ?",
                (src, k)).fetchall()
        else:
            rows = con.execute(
                "SELECT dst_doc, weight w FROM affinity WHERE src_doc=? AND signal=? ORDER BY w DESC LIMIT ?",
                (src, signal, k)).fetchall()
        return [r["dst_doc"] for r in rows]

    signals = ["co_citation", "coupling", "entity", "fused"]
    res = {s: {"recall": 0.0, "mrr": 0.0} for s in signals}
    for src in seeds:
        rel = cited[src]
        for s in signals:
            ranked = rank(src, s)
            hitset = set(ranked) & rel
            res[s]["recall"] += len(hitset) / len(rel)
            for i, d in enumerate(ranked):
                if d in rel:
                    res[s]["mrr"] += 1 / (i + 1)
                    break
    N = len(seeds)
    return {"n": N, **{s: {"recall@k": res[s]["recall"] / N, "mrr": res[s]["mrr"] / N} for s in signals}}
