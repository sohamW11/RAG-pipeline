#!/usr/bin/env python3
"""locate.py — type anything, find the document, see where it's stored.

Give it a free-form query — a doc id, some title words, a year, a section name,
or any mix — and it works out what you mean, ranks the matches, and shows WHERE
each one lives: the source PDF, the parsed JSON, whether it's in the knowledge
base, its status/version-thread, and the SEBI URLs. Stdlib only.

Examples
  python locate.py 24202
  python locate.py insider trading
  python locate.py mutual fund master circular 2013
  python locate.py icdr regulations 2018
  python locate.py asba --show          # + parsed-content summary
  python locate.py "portfolio manager" --json
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
CRAWLER_DB = BASE / "crawler.db"
KNOW_DB = BASE / "knowledge" / "knowledge.db"
PARSED = BASE / "preprocess" / "parsed"
STORAGE = BASE / "storage-data"

import sys  # noqa: E402
sys.path.insert(0, str(BASE / "knowledge"))
try:
    import memory as _memory  # unified relatedness fusion (needs numpy)
except Exception:
    _memory = None


def find_pdf(doc_id: str) -> list[str]:
    hits = glob.glob(str(STORAGE / "**" / f"*_{doc_id}.pdf"), recursive=True)
    return [str(Path(h).relative_to(BASE)) for h in hits]


def know_row(con, doc_id: str) -> dict | None:
    if con is None:
        return None
    r = con.execute("SELECT status,thread_id,superseded_by FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
    return dict(r) if r else None


def parsed_summary(doc_id: str) -> dict | None:
    p = PARSED / f"{doc_id}.json"
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text())
    except Exception:
        return {"error": "unreadable JSON"}
    els = d.get("elements", [])
    heading = next((e.get("text") for e in els if e.get("type") == "heading" and e.get("text")), None)
    return {"page_count": d.get("page_count"), "element_count": len(els),
            "types": dict(Counter(e.get("type") for e in els)),
            "tables": d.get("stats", {}).get("tables_found"), "first_heading": heading}


def _score(title: str, keywords: str, words: list[str]) -> int:
    tl, kl = (title or "").lower(), (keywords or "").lower()
    s = 0
    for w in words:
        if w in tl:
            s += 3
        elif w in kl:
            s += 1
    return s


def smart_search(craw, query: str, limit: int) -> tuple[list, dict]:
    """Interpret a free-form query into id / year / words, then rank matches."""
    tokens = query.split()
    year, ids, words = None, [], []
    for t in tokens:
        if re.fullmatch(r"(19|20)\d\d", t):                 # a year
            year = t
        elif t.isdigit() and len(t) >= 3 and craw.execute(
                "SELECT 1 FROM documents WHERE document_number=?", (t,)).fetchone():
            ids.append(t)                                   # a real doc id
        else:
            words.append(t.lower())

    scored: dict[str, tuple] = {}
    sel = "SELECT *, document_type AS doc_type FROM documents"

    for i in ids:                                           # exact id → top
        r = craw.execute(f"{sel} WHERE document_number=?", (i,)).fetchone()
        if r:
            scored[i] = (r, 10_000)

    if words:
        def run(joiner):
            cl = [" (title LIKE ? OR keywords LIKE ?) " for _ in words]
            params = []
            for w in words:
                params += [f"%{w}%", f"%{w}%"]
            sql = f"{sel} WHERE " + joiner.join(cl)
            if year:
                sql += " AND substr(publication_date,1,4)=?"
                params.append(year)
            return craw.execute(sql, params).fetchall()

        cand = run(" AND ")                                 # all words present
        if not cand and len(words) > 1:
            cand = run(" OR ")                              # fall back to any word
        for r in cand:
            did = str(r["document_number"])
            if did not in scored:
                scored[did] = (r, _score(r["title"], r["keywords"], words))
    elif year and not ids:                                  # only a year
        for r in craw.execute(f"{sel} WHERE substr(publication_date,1,4)=? "
                              "ORDER BY publication_date DESC", (year,)).fetchall():
            scored[str(r["document_number"])] = (r, 0)

    ranked = sorted(scored.values(), key=lambda x: (x[1], (x[0]["publication_date"] or "")), reverse=True)
    return [r for r, _ in ranked][:limit], {"ids": ids, "year": year, "words": words}


def graph_for(know, doc_id: str) -> dict | None:
    """Pull a doc's connections from knowledge.db for the ASCII graph."""
    if know is None:
        return None
    out = [dict(r) for r in know.execute(
        "SELECT e.relation, e.dst_doc did, d.title, d.status FROM edges e "
        "LEFT JOIN documents d ON d.doc_id=e.dst_doc WHERE e.src_doc=? "
        "ORDER BY e.confidence DESC LIMIT 6", (doc_id,))]
    inc = [dict(r) for r in know.execute(
        "SELECT e.relation, e.src_doc did, d.title FROM edges e "
        "LEFT JOIN documents d ON d.doc_id=e.src_doc WHERE e.dst_doc=? "
        "ORDER BY e.confidence DESC LIMIT 4", (doc_id,))]
    aff = [dict(r) for r in know.execute(
        "SELECT a.signal, a.dst_doc did, a.weight, d.title FROM affinity a "
        "LEFT JOIN documents d ON d.doc_id=a.dst_doc WHERE a.src_doc=? "
        "ORDER BY a.weight DESC LIMIT 5", (doc_id,))]
    fused = []
    if _memory is not None:
        try:
            fused = _memory.relatedness(know, doc_id, k=6, use_citation=True)
        except Exception:
            fused = []
    trow = know.execute("SELECT thread_id FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
    thread = None
    if trow and trow["thread_id"] is not None:
        thread = [dict(r) for r in know.execute(
            "SELECT doc_id, effective_from, effective_to, status FROM documents "
            "WHERE thread_id=? ORDER BY thread_seq", (trow["thread_id"],))]
    return {"out": out, "inc": inc, "aff": aff, "fused": fused, "thread": thread}


def render_graph(rec: dict, g: dict | None) -> None:
    print()
    if g is None:
        print("  (no graph — this document isn't in the knowledge base yet)")
        return
    did, title = rec["doc_id"], (rec["title"] or "")[:46]
    print(f"  ◆ {did}  {title}   [{rec['status'] or '?'}]"
          + (f"  · thread {rec['thread_id']}" if rec["thread_id"] else ""))
    print("  │")

    rows: list[str] = []
    for e in g["out"]:                                   # this doc → others (typed)
        st = f"  [{e['status']}]" if e.get("status") else ""
        rows.append(f"{e['relation']:<12}─▶  {e['did']}  {(e['title'] or '?')[:38]}{st}")
    for e in g["inc"]:                                   # others → this doc
        rows.append(f"{'cited-by':<12}◀─  {e['did']}  {(e['title'] or '?')[:38]}")
    shown = {e["did"] for e in g["out"]} | {e["did"] for e in g["inc"]}
    if g.get("fused"):                                   # UNIFIED fused relatedness
        added = 0
        for f in g["fused"]:
            if f["doc_id"] in shown or f["doc_id"] == did:
                continue
            rows.append(f"{'related':<12}⊕   {f['doc_id']}  {(f['title'] or '?')[:30]}  "
                        f"({f['score']:.3f} · {','.join(f['signals'][:3])})")
            added += 1
            if added >= 5:
                break
    else:                                                # fallback: raw affinity
        SIG = {"co_citation": "co-cited", "coupling": "shared-refs", "entity": "shared-topic"}
        for a in g["aff"]:
            rows.append(f"{SIG.get(a['signal'], a['signal']):<12}··  {a['did']}  "
                        f"{(a['title'] or '?')[:32]}  ({a['weight']:.2f})")

    has_thread = bool(g["thread"] and len(g["thread"]) > 1)
    for idx, row in enumerate(rows):
        conn = "  └─" if (idx == len(rows) - 1 and not has_thread) else "  ├─"
        print(f"{conn} {row}")
    if has_thread:
        marks = []
        for m in g["thread"]:
            y = (m["effective_from"] or "?")[:4]
            live = m["effective_to"] is None and m["status"] != "superseded"
            marks.append(f"[{m['doc_id']} ●now]" if live else y)
        chain = " → ".join(marks if len(marks) <= 8 else marks[:3] + ["…"] + marks[-3:])
        print(f"  └─ version thread ({len(g['thread'])}):  {chain}")
    if not rows and not has_thread:
        print("  └─ (no connections recorded)")
    print()


def make_rec(r, know, show: bool) -> dict:
    did = str(r["document_number"])
    is_parsed = (PARSED / f"{did}.json").is_file()
    kb = know_row(know, did)
    rec = {
        "doc_id": did, "title": r["title"], "date": (r["publication_date"] or "")[:10],
        "section": r["category_name"], "department": r["department"],
        "status": (kb or {}).get("status"), "thread_id": (kb or {}).get("thread_id"),
        "stored": {
            "pdf": find_pdf(did) or None,
            "parsed_json": f"preprocess/parsed/{did}.json" if is_parsed else None,
            "in_knowledge_db": bool(kb),
            "sebi_page": r["source_url"] or r["html_url"],
            "original_pdf_url": r["pdf_url"],
        },
    }
    if show:
        rec["content"] = parsed_summary(did)
    return rec


def render(rec: dict, show: bool, best: bool) -> None:
    s = rec["stored"]
    print("─" * 78)
    tag = "  ★ BEST MATCH" if best else ""
    print(f"  doc_id {rec['doc_id']}   [{rec['status'] or 'not-parsed'}]"
          + (f"  thread {rec['thread_id']}" if rec["thread_id"] else "") + tag)
    print(f"  {rec['title']}")
    print(f"  {rec['date']} · {rec['section']}" + (f" · {rec['department']}" if rec["department"] else ""))
    print("  stored at:")
    print(f"    PDF          : {s['pdf'][0] if s['pdf'] else 'NOT ON DISK'}"
          + (f'  (+{len(s["pdf"])-1} more)' if s["pdf"] and len(s["pdf"]) > 1 else ""))
    print(f"    parsed JSON  : {s['parsed_json'] or 'NOT PARSED YET'}")
    print(f"    knowledge.db : {'yes' if s['in_knowledge_db'] else 'no'}")
    print(f"    SEBI page    : {s['sebi_page']}")
    print(f"    original PDF : {s['original_pdf_url']}")
    if show and rec.get("content"):
        c = rec["content"]
        print(f"  content: {c.get('page_count')} pages · {c.get('element_count')} elements · "
              f"{c.get('tables')} tables · {c.get('types')}")
        if c.get("first_heading"):
            print(f"    first heading: {c['first_heading'][:72]}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Type anything — id, title words, a year — and locate the document(s).",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("Examples")[1])
    ap.add_argument("query", nargs="*", help="free-form: id / words / year / mix")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--show", action="store_true", help="also print parsed-content summary")
    ap.add_argument("--graph", action="store_true", help="draw each doc's connection graph (ASCII)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()
    if not a.query:
        ap.print_help()
        return

    craw = sqlite3.connect(CRAWLER_DB); craw.row_factory = sqlite3.Row
    know = None
    if KNOW_DB.is_file():
        know = sqlite3.connect(KNOW_DB); know.row_factory = sqlite3.Row

    rows, interp = smart_search(craw, " ".join(a.query), a.limit)
    recs = [make_rec(r, know, a.show) for r in rows]

    if a.json:
        print(json.dumps(recs, indent=2))
        return
    if not recs:
        print(f"No match for {' '.join(a.query)!r}.")
        return
    bits = []
    if interp["ids"]:
        bits.append("id=" + ",".join(interp["ids"]))
    if interp["words"]:
        bits.append("text=" + " ".join(interp["words"]))
    if interp["year"]:
        bits.append("year=" + interp["year"])
    print(f"  interpreted as: {' · '.join(bits)}   ({len(recs)} match{'es' if len(recs) != 1 else ''})")
    for n, rec in enumerate(recs):
        render(rec, a.show, best=(n == 0 and len(recs) > 1))
        if a.graph:
            render_graph(rec, graph_for(know, rec["doc_id"]) if rec["stored"]["in_knowledge_db"] else None)
    print("─" * 78)


if __name__ == "__main__":
    main()
