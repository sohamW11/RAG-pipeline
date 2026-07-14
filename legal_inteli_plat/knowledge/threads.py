"""Temporal version-threads over the knowledge base.

A "thread" is a version lineage of the SAME rule across time — e.g. the original
2018 circular, its 2020 amendment, its 2022 replacement. Threads let the agent
answer with the *in-force* version and show the history.

How a thread is built:
  1. effective_from per doc = a parsed "w.e.f." / "last amended" date, else its date.
  2. INFERRED supersession edges — SEBI titles are systematic, so two docs whose
     title *core* (title minus amendment/year/w.e.f. noise) and subsection match
     are the same rule at different times. We add supersedes(newer→older) edges,
     flagged low-confidence, where no explicit edge already exists. (This is the
     precise, high-signal instance of "same subject + close dates" for this corpus.)
  3. status is recomputed so inferred edges update currency.
  4. thread_id = connected component over supersedes+amends (explicit ∪ inferred);
     members ordered by effective_from give thread_seq; effective_to(x) =
     effective_from of the next member (NULL = still in force).

Run at the end of build_memory.py. Stdlib only.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict

import kb

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
# "w.e.f. March 7, 2016" / "with effect from 7 March 2016" / "last amended on March 7, 2016"
_WEF = re.compile(
    r"(?:w\.?e\.?f\.?|with effect from|last amended (?:on|w\.?e\.?f\.?))\s*"
    r"([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]+\.?\s+\d{4})", re.I)


def parse_wef(title: str) -> str | None:
    """Extract an effective date stated in the title -> 'YYYY-MM-DD' (or None)."""
    m = _WEF.search(title or "")
    if not m:
        return None
    s = m.group(1)
    a = re.match(r"([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", s)      # Month D, YYYY
    b = re.match(r"(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})", s)        # D Month YYYY
    if a:
        mon, day, yr = _MONTHS.get(a.group(1)[:3].lower()), int(a.group(2)), int(a.group(3))
    elif b:
        day, mon, yr = int(b.group(1)), _MONTHS.get(b.group(2)[:3].lower()), int(b.group(3))
    else:
        return None
    return f"{yr:04d}-{mon:02d}-{day:02d}" if mon else None


def title_core(title: str) -> str:
    """Strip amendment/year/w.e.f. noise so version titles collapse to one key."""
    t = (title or "").lower()
    t = re.sub(r"\((?:[^()]*?(?:w\.?e\.?f|amendment|last amended|effective)[^()]*)\)", " ", t)
    t = re.sub(r"\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b", " ", t)
    t = re.sub(r"\bamendment\b|\bamended\b|\blast amended\b|\bas amended\b|\bw\.?e\.?f\.?\b", " ", t)
    t = re.sub(r"\b(?:19|20)\d\d\b", " ", t)
    t = re.sub(r"[^a-z ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _ensure_columns(con: sqlite3.Connection) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(documents)")}
    for name, decl in [("effective_from", "TEXT"), ("effective_to", "TEXT"),
                       ("thread_id", "INTEGER"), ("thread_seq", "INTEGER")]:
        if name not in cols:
            con.execute(f"ALTER TABLE documents ADD COLUMN {name} {decl}")


def build_threads(con: sqlite3.Connection) -> dict:
    _ensure_columns(con)
    docs = con.execute("SELECT doc_id, title, subsection, date FROM documents").fetchall()

    # 1) effective_from
    eff = {}
    for r in docs:
        e = parse_wef(r["title"]) or (r["date"] or "")
        eff[r["doc_id"]] = e or None
        con.execute("UPDATE documents SET effective_from=? WHERE doc_id=?", (eff[r["doc_id"]], r["doc_id"]))

    # 2) group same-INSTRUMENT versions by title-core, and ONLY in versioned
    #    subsections. Regulations/master circulars/rules/acts are re-issued as
    #    versions; generic one-off circulars are NOT (so they aren't title-threaded
    #    — that would merge unrelated announcements). A thread = one such group.
    VERSIONED = {"Regulations", "Master Circulars", "Rules", "Acts"}
    groups = defaultdict(list)
    for r in docs:
        if r["subsection"] not in VERSIONED:
            continue
        core = title_core(r["title"])
        if len(core) >= 12:                        # skip trivial/empty cores
            groups[(core, r["subsection"])].append(r["doc_id"])

    con.execute("UPDATE documents SET thread_id=NULL, thread_seq=NULL, effective_to=NULL")
    inferred, tid, n_threaded, largest = 0, 0, 0, 0
    for members in groups.values():
        members = [d for d in members if eff.get(d)]
        if len(members) < 2:
            continue
        members.sort(key=lambda d: eff[d])         # oldest -> newest
        tid += 1
        n_threaded += len(members)
        largest = max(largest, len(members))
        for seq, d in enumerate(members):
            nxt = members[seq + 1] if seq + 1 < len(members) else None
            con.execute("UPDATE documents SET thread_id=?, thread_seq=?, effective_to=? WHERE doc_id=?",
                        (tid, seq, eff.get(nxt) if nxt else None, d))
            if nxt:                                # inferred supersedes: newer -> older (flagged)
                cur = con.execute(
                    "INSERT OR IGNORE INTO edges(src_doc,dst_doc,relation,evidence,confidence) VALUES(?,?,?,?,?)",
                    (nxt, d, "supersedes", "inferred: same-title version thread", 0.5))
                inferred += cur.rowcount
    con.commit()

    # 3) refresh currency with the inferred edges included
    kb.compute_status(con)
    con.commit()
    return {"threads": tid, "threaded_docs": n_threaded, "inferred_edges": inferred, "largest": largest}


# ------------------------------------------------------------------ queries
def thread_of(con: sqlite3.Connection, doc_id: str) -> dict | None:
    """Ordered version timeline containing doc_id (None if the doc is standalone)."""
    row = con.execute("SELECT thread_id FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
    if not row or row["thread_id"] is None:
        return None
    members = con.execute(
        """SELECT doc_id, title, subsection, effective_from, effective_to, thread_seq, status, date
           FROM documents WHERE thread_id=? ORDER BY thread_seq""", (row["thread_id"],)).fetchall()
    out = []
    for m in members:
        out.append({"doc_id": m["doc_id"], "title": m["title"], "subsection": m["subsection"],
                    "effective_from": m["effective_from"], "effective_to": m["effective_to"],
                    "seq": m["thread_seq"], "status": m["status"],
                    "in_force": m["effective_to"] is None and m["status"] != "superseded"})
    return {"thread_id": row["thread_id"], "members": out,
            "in_force_doc_id": next((m["doc_id"] for m in out if m["in_force"]), out[-1]["doc_id"] if out else None)}


def current_in_force(con: sqlite3.Connection, thread_id: int, as_of: str | None = None) -> str | None:
    rows = con.execute(
        "SELECT doc_id, effective_from, effective_to FROM documents WHERE thread_id=? ORDER BY thread_seq",
        (thread_id,)).fetchall()
    if not rows:
        return None
    if as_of is None:
        return rows[-1]["doc_id"]                    # latest = current
    for r in rows:                                    # as-of: interval containing the date
        lo, hi = r["effective_from"], r["effective_to"]
        if (lo is None or lo <= as_of) and (hi is None or as_of < hi):
            return r["doc_id"]
    return rows[-1]["doc_id"]
