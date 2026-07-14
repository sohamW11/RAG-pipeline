"""Build knowledge.db from the parsed corpus + crawler metadata (slices 1 & 2).

    python build.py                 # builds ./knowledge.db
    python build.py --demo "insider trading"   # build + a sample query

Steps: load crawler metadata, chunk every parsed doc into FTS, harvest each
doc's own circular ID, extract citations, then resolve them to edges. Prints
coverage + reference-resolution metrics at the end. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import kb
import references as refs

HERE = Path(__file__).resolve().parent
PARSED = HERE.parent / "preprocess" / "parsed"
CRAWLER_DB = HERE.parent / "crawler.db"


def _load_metadata(crawler_db: Path) -> dict[str, dict]:
    meta: dict[str, dict] = {}
    if not crawler_db.is_file():
        return meta
    con = sqlite3.connect(str(crawler_db))
    con.row_factory = sqlite3.Row
    for r in con.execute("SELECT * FROM documents"):
        d = dict(r)
        num = str(d.get("document_number") or "")
        pub = (d.get("publication_date") or "")[:10]
        meta[num] = {"department": d.get("department"), "category_name": d.get("category_name"),
                     "doc_type": d.get("document_type"), "pub_date": pub}
    con.close()
    return meta


def build(parsed_dir: Path, crawler_db: Path, out: Path) -> dict:
    if out.exists():
        out.unlink()
    con = kb.connect(out)
    if not kb.fts5_available(con):
        raise SystemExit("FTS5 not available in this SQLite build — cannot proceed.")
    con.executescript(kb.SCHEMA)
    meta = _load_metadata(crawler_db)

    files = [f for f in parsed_dir.glob("*.json") if "manifest" not in f.name]
    circ_map: dict[str, str] = {}          # normalized circular_no -> doc_id
    date_map: dict[str, list[str]] = defaultdict(list)  # YYYY-MM-DD -> [doc_id]
    n_chunks = n_refs = 0

    con.execute("BEGIN")
    for f in files:
        try:
            doc = json.loads(f.read_text())
        except Exception:
            continue
        doc_id = str(doc.get("doc_id", f.stem))
        m = meta.get(doc_id, {})
        own_id = refs.harvest_own_id(doc.get("elements", []))
        date = (doc.get("date") or m.get("pub_date") or "")[:10]
        con.execute(
            """INSERT OR REPLACE INTO documents
               (doc_id,title,date,subsection,department,doc_type,url,circular_no,n_elements,page_count)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, doc.get("title"), date, doc.get("subsection"), m.get("department"),
             m.get("doc_type"), doc.get("source_url"), own_id,
             len(doc.get("elements", [])), doc.get("page_count")))
        if own_id and own_id not in circ_map:
            circ_map[own_id] = doc_id
        if date:
            date_map[date].append(doc_id)

        for c in kb.iter_chunks(doc):
            con.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?)",
                        (c["chunk_id"], doc_id, c["page"], c["section"], c["kind"], c["text"]))
            con.execute("INSERT INTO chunks_fts(text,chunk_id,doc_id) VALUES(?,?,?)",
                        (c["text"], c["chunk_id"], doc_id))
            n_chunks += 1

        # citations: scan text elements
        cites = []
        for e in doc.get("elements", []):
            if e.get("text"):
                cites.extend(refs.extract(e["text"]))
        for r in refs.dedup(cites):
            con.execute(
                "INSERT INTO doc_refs(doc_id,raw,cited_no,cited_date,relation) VALUES(?,?,?,?,?)",
                (doc_id, r["raw"], r["cited_no"], r["cited_date"], r["relation"]))
            n_refs += 1
    con.execute("COMMIT")

    # ---- resolve citations -> edges ----
    resolved = 0
    con.execute("BEGIN")
    for r in con.execute("SELECT * FROM doc_refs").fetchall():
        src, cited_no, cited_date, rel = r["doc_id"], r["cited_no"], r["cited_date"], r["relation"]
        dst, conf = None, 0.0
        if cited_no and cited_no in circ_map:
            dst, conf = circ_map[cited_no], 0.9
        elif cited_date and len(date_map.get(cited_date, [])) == 1:
            dst, conf = date_map[cited_date][0], 0.55        # unique date match
        if dst and dst != src:
            con.execute(
                """INSERT OR IGNORE INTO edges(src_doc,dst_doc,relation,evidence,confidence)
                   VALUES(?,?,?,?,?)""", (src, dst, rel, r["raw"], conf))
            con.execute("UPDATE doc_refs SET resolved=? WHERE id=?", (dst, r["id"]))
            resolved += 1
    con.execute("COMMIT")

    # ---- derive master-circular supersession chains (conservative) ----
    # Same subject + subsection, newer supersedes older. Only for titles that
    # clearly start with "Master Circular"; tagged confidence 0.7 so text-extracted
    # supersedes (0.9, inserted above via OR IGNORE) stays authoritative.
    derived = 0
    groups: dict[tuple, list] = defaultdict(list)
    for r in con.execute("SELECT doc_id,title,date,subsection FROM documents "
                          "WHERE lower(title) LIKE 'master circular%'"):
        subject = re.sub(r"\(.*?\)", "", (r["title"] or "")).lower()
        subject = re.sub(r"[^a-z ]", " ", subject)
        subject = re.sub(r"\s+", " ", subject).strip()
        groups[(subject, r["subsection"])].append((r["date"] or "", r["doc_id"]))
    con.execute("BEGIN")
    for members in groups.values():
        members = [m for m in members if m[0]]           # need a date to order
        members.sort()                                    # oldest -> newest
        for older, newer in zip(members, members[1:]):
            if older[1] != newer[1]:
                con.execute(
                    """INSERT OR IGNORE INTO edges(src_doc,dst_doc,relation,evidence,confidence)
                       VALUES(?,?,?,?,?)""",
                    (newer[1], older[1], "supersedes", "master-circular chain", 0.7))
                derived += 1
    con.execute("COMMIT")

    # ---- compute currency status from the edges ----
    kb.compute_status(con)

    n_docs = con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    n_edges = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    with_circ = con.execute("SELECT COUNT(*) FROM documents WHERE circular_no IS NOT NULL").fetchone()[0]
    connected = con.execute(
        "SELECT COUNT(DISTINCT doc_id) FROM (SELECT src_doc doc_id FROM edges UNION SELECT dst_doc FROM edges)"
    ).fetchone()[0]
    rel_counts = con.execute("SELECT relation,COUNT(*) FROM edges GROUP BY relation ORDER BY 2 DESC").fetchall()
    status_counts = dict(con.execute("SELECT status,COUNT(*) FROM documents GROUP BY status").fetchall())
    con.commit()
    con.close()
    return {"docs": n_docs, "chunks": n_chunks, "refs": n_refs, "edges": n_edges,
            "resolved": resolved, "with_circ": with_circ, "connected": connected,
            "rel_counts": [(r[0], r[1]) for r in rel_counts], "derived_chains": derived,
            "status": status_counts}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(kb.DEFAULT_DB))
    ap.add_argument("--demo", default=None, help="run a sample query after building")
    args = ap.parse_args()
    out = Path(args.out)
    print(f"building {out} from {PARSED} …")
    t0 = datetime.now()
    s = build(PARSED, CRAWLER_DB, out)
    dt = (datetime.now() - t0).total_seconds()
    print(f"\n  docs indexed      {s['docs']:,}")
    print(f"  chunks (FTS5)     {s['chunks']:,}")
    print(f"  citations found   {s['refs']:,}")
    print(f"  own circular IDs  {s['with_circ']:,} / {s['docs']:,} docs "
          f"({s['with_circ']/max(s['docs'],1)*100:.0f}%)")
    print(f"  resolved edges    {s['edges']:,}  "
          f"(resolution rate {s['resolved']/max(s['refs'],1)*100:.0f}% of citations)")
    print(f"  docs in graph     {s['connected']:,} / {s['docs']:,} "
          f"({s['connected']/max(s['docs'],1)*100:.0f}% connected)")
    print(f"  edge types        " + ", ".join(f"{r}:{n}" for r, n in s["rel_counts"]))
    print(f"  master chains     +{s['derived_chains']} derived supersedes edges")
    st = s["status"]
    print(f"  doc currency      live:{st.get('live',0)}  "
          f"superseded:{st.get('superseded',0)}  amended:{st.get('amended',0)}")
    print(f"  built in {dt:.1f}s")

    if args.demo:
        con = kb.connect(out)
        print(f"\n=== search: {args.demo!r} ===")
        for h in kb.search(con, args.demo, k=5):
            print(f"  [{h['score']:.1f}] doc {h['doc_id']} p.{h['page']} :: {(h['title'] or '')[:48]}")
        top = kb.search(con, args.demo, k=1)
        if top:
            nb = kb.neighbors(con, top[0]["doc_id"])
            print(f"\n=== related to doc {top[0]['doc_id']} ({(top[0]['title'] or '')[:40]}) ===")
            for e in nb["outgoing"][:5]:
                print(f"  --{e['relation']}--> {e['dst_doc']}  {(e['title'] or '?')[:44]}")
            for e in nb["incoming"][:5]:
                print(f"  <--{e['relation']}-- {e['src_doc']}  {(e['title'] or '?')[:44]}")
        con.close()


if __name__ == "__main__":
    main()
