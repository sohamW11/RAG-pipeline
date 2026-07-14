"""Knowledge base over the parsed SEBI corpus — SQLite + FTS5 + a reference graph.

Slice 1 (retrieval backbone): documents, section-aware chunks, and an FTS5 index
that replaces the in-memory BM25 pickle with a persistent, incrementally
updatable store queried by SQLite's built-in bm25().

Slice 2 (relations): each document's own circular ID is harvested, citations are
extracted from the text (see references.py), resolved to doc_ids, and written as
typed edges (supersedes / amends / read_with / references) so the corpus can be
traversed as a graph.

Stdlib only. Build with `python build.py`; query helpers below power the API.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import references as refs

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "knowledge.db"

SCHEMA = """
CREATE TABLE documents(
  doc_id TEXT PRIMARY KEY, title TEXT, date TEXT, subsection TEXT, department TEXT,
  doc_type TEXT, url TEXT, circular_no TEXT, n_elements INTEGER, page_count INTEGER,
  status TEXT DEFAULT 'live', superseded_by TEXT
);
CREATE TABLE chunks(
  chunk_id TEXT PRIMARY KEY, doc_id TEXT, page INTEGER, section TEXT, kind TEXT, text TEXT
);
CREATE INDEX idx_chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  text, chunk_id UNINDEXED, doc_id UNINDEXED, tokenize='porter unicode61'
);
CREATE TABLE doc_refs(
  id INTEGER PRIMARY KEY, doc_id TEXT, raw TEXT, cited_no TEXT, cited_date TEXT,
  relation TEXT, resolved TEXT
);
CREATE TABLE edges(
  src_doc TEXT, dst_doc TEXT, relation TEXT, evidence TEXT, confidence REAL,
  PRIMARY KEY(src_doc, dst_doc, relation)
);
CREATE INDEX idx_edges_src ON edges(src_doc);
CREATE INDEX idx_edges_dst ON edges(dst_doc);
"""

_MAX_CHARS = 900
_TOKEN = re.compile(r"[A-Za-z0-9]+")


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    return con


def fts5_available(con: sqlite3.Connection) -> bool:
    try:
        con.execute("CREATE VIRTUAL TABLE _t USING fts5(x)")
        con.execute("DROP TABLE _t")
        return True
    except sqlite3.OperationalError:
        return False


# ---------------------------------------------------------------- chunking
def _flatten_table(rows: list[list[str]]) -> str:
    out = []
    for r in rows[:12]:
        cells = [str(c).strip() for c in r if str(c).strip()]
        if cells:
            out.append(" | ".join(cells))
    return "  ;  ".join(out)


def iter_chunks(doc: dict) -> list[dict]:
    doc_id = str(doc.get("doc_id", "?"))
    els = sorted(doc.get("elements", []),
                 key=lambda e: (e.get("part", 0), e.get("page", 0), e.get("reading_order", 0)))
    out: list[dict] = []
    st = {"section": "", "buf": [], "page": 1, "n": 0}

    def flush():
        text = " ".join(st["buf"]).strip()
        st["buf"] = []
        if len(text) < 25:
            return
        for i in range(0, len(text), _MAX_CHARS):
            out.append({"chunk_id": f"{doc_id}::c{st['n']}", "page": st["page"],
                        "section": st["section"], "kind": "text", "text": text[i:i + _MAX_CHARS]})
            st["n"] += 1

    for e in els:
        st["page"] = e.get("page", st["page"])
        t = e.get("type")
        if t == "heading":
            flush()
            st["section"] = (e.get("text") or "").strip()[:120]
            if st["section"]:
                st["buf"].append(st["section"])
        elif t == "table" and e.get("table"):
            flush()
            out.append({"chunk_id": f"{doc_id}::t{st['n']}", "page": st["page"],
                        "section": st["section"], "kind": "table",
                        "text": f"[table] {_flatten_table(e['table'])}"})
            st["n"] += 1
        elif e.get("text"):
            st["buf"].append(e["text"].strip())
            if sum(len(x) for x in st["buf"]) >= _MAX_CHARS:
                flush()
    flush()
    return out


# ---------------------------------------------------------------- retrieval
def search(con: sqlite3.Connection, query: str, k: int = 8) -> list[dict]:
    terms = _TOKEN.findall(query.lower())
    if not terms:
        return []
    match = " OR ".join(terms)
    rows = con.execute(
        """SELECT f.doc_id, f.chunk_id, d.title, d.subsection, d.date, d.url, d.circular_no,
                  c.page, c.section, c.kind, c.text, bm25(chunks_fts) AS score
           FROM chunks_fts f
           JOIN chunks c ON c.chunk_id = f.chunk_id
           JOIN documents d ON d.doc_id = f.doc_id
           WHERE chunks_fts MATCH ?
           ORDER BY score LIMIT ?""", (match, k)).fetchall()
    return [dict(r) for r in rows]


def compute_status(con: sqlite3.Connection) -> None:
    """Derive each doc's currency from the edges (call after edges are built).

    superseded  = target of a `supersedes` edge  -> dead; point at the newest src.
    amended     = target of an `amends` edge      -> live but modified.
    live        = otherwise.
    `supersedes` wins over `amends` when both apply.
    """
    con.execute("UPDATE documents SET status='live', superseded_by=NULL")
    # amended first, superseded second so superseded overrides
    con.execute("""UPDATE documents SET status='amended'
                   WHERE doc_id IN (SELECT DISTINCT dst_doc FROM edges WHERE relation='amends')""")
    # Pick the superseding doc, but ONLY from temporally-valid edges: the replacement
    # must be newer-or-equal (unknown dates allowed). This drops backward supersession
    # from noisy date-resolved citations.
    con.execute("""UPDATE documents SET superseded_by=(
                     SELECT e.src_doc FROM edges e JOIN documents s ON s.doc_id=e.src_doc
                     WHERE e.dst_doc=documents.doc_id AND e.relation='supersedes'
                       AND (documents.date IS NULL OR documents.date='' OR s.date IS NULL
                            OR s.date='' OR s.date >= documents.date)
                     ORDER BY e.confidence DESC, s.date DESC LIMIT 1)
                   WHERE doc_id IN (SELECT DISTINCT dst_doc FROM edges WHERE relation='supersedes')""")
    con.execute("UPDATE documents SET status='superseded' WHERE superseded_by IS NOT NULL")
    con.commit()


def current_version(con: sqlite3.Connection, doc_id: str, _seen=None) -> str:
    """Follow supersedes pointers to the live replacement (guards against cycles)."""
    _seen = _seen or set()
    if doc_id in _seen:
        return doc_id
    _seen.add(doc_id)
    row = con.execute("SELECT status, superseded_by FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
    if row and row["status"] == "superseded" and row["superseded_by"]:
        return current_version(con, row["superseded_by"], _seen)
    return doc_id


def status_map(con: sqlite3.Connection, doc_ids: list[str]) -> dict:
    if not doc_ids:
        return {}
    qs = ",".join("?" * len(doc_ids))
    out = {}
    for r in con.execute(
        f"""SELECT d.doc_id, d.status, d.superseded_by, s.title AS sup_title, s.date AS sup_date
            FROM documents d LEFT JOIN documents s ON s.doc_id=d.superseded_by
            WHERE d.doc_id IN ({qs})""", doc_ids):
        out[r["doc_id"]] = {"status": r["status"], "superseded_by": r["superseded_by"],
                            "superseded_by_title": r["sup_title"], "superseded_by_date": r["sup_date"]}
    return out


def neighbors(con: sqlite3.Connection, doc_id: str) -> dict:
    out = con.execute(
        """SELECT e.relation, e.dst_doc, e.confidence, d.title, d.subsection, d.date
           FROM edges e LEFT JOIN documents d ON d.doc_id = e.dst_doc
           WHERE e.src_doc = ? ORDER BY e.confidence DESC""", (doc_id,)).fetchall()
    inc = con.execute(
        """SELECT e.relation, e.src_doc, e.confidence, d.title, d.subsection, d.date
           FROM edges e LEFT JOIN documents d ON d.doc_id = e.src_doc
           WHERE e.dst_doc = ? ORDER BY e.confidence DESC""", (doc_id,)).fetchall()
    return {"outgoing": [dict(r) for r in out], "incoming": [dict(r) for r in inc]}


def expand(con: sqlite3.Connection, doc_ids: list[str]) -> list[dict]:
    """Graph expansion: related docs one hop from the retrieved set."""
    if not doc_ids:
        return []
    qs = ",".join("?" * len(doc_ids))
    rows = con.execute(
        f"""SELECT DISTINCT e.src_doc, e.dst_doc, e.relation, e.confidence, d.title
            FROM edges e JOIN documents d ON d.doc_id = e.dst_doc
            WHERE e.src_doc IN ({qs})
            ORDER BY e.confidence DESC LIMIT 20""", doc_ids).fetchall()
    return [dict(r) for r in rows]
