"""KB-backed retriever: knowledge.db (FTS5 + reference graph) behind the same
interface the agent already expects (search / ranked_docs / expand + stats).

Returns rag.Chunk objects so rag.py's answer/citation helpers work unchanged.
Falls back is not needed — if knowledge.db is missing, server.py keeps the pickle.
FTS5 bm25() scores are negative (lower = better); we return positive relevance
(= -bm25) so downstream ranking/thresholds read naturally.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path

import rag

# knowledge/ is a sibling package; put it on the path so `import kb` resolves
_KNOW = Path(__file__).resolve().parent.parent / "knowledge"
sys.path.insert(0, str(_KNOW))
import kb as knowledge_kb  # noqa: E402
try:
    import memory as knowledge_memory  # noqa: E402
except Exception:
    knowledge_memory = None


class KBIndex:
    """Duck-typed replacement for rag.RagIndex, backed by knowledge.db."""

    def __init__(self, db_path: Path | None = None, use_vector: bool = False):
        self.db_path = str(db_path or knowledge_kb.DEFAULT_DB)
        # one shared connection, guarded by a lock (ThreadingHTTPServer uses threads,
        # so check_same_thread must be off)
        self._con = sqlite3.connect(self.db_path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA query_only=ON")
        self._lock = threading.Lock()
        # optional semantic booster: FTS stays primary, semantic adds conceptual recall
        self.vindex = None
        if use_vector:
            try:
                import vector
                if vector.available():
                    self.vindex = vector.VectorIndex()
            except Exception as exc:
                print(f"vector booster unavailable ({exc}); FTS only")
        self.n_passages = self._scalar("SELECT COUNT(*) FROM chunks")
        self.n_docs = self._scalar("SELECT COUNT(*) FROM documents")
        self.n_edges = self._scalar("SELECT COUNT(*) FROM edges")
        rows = self._q("SELECT subsection, COUNT(*) n FROM documents "
                       "WHERE subsection<>'' GROUP BY subsection ORDER BY n DESC LIMIT 6")
        self.top_subsections = [r["subsection"] for r in rows]

    # -- low-level helpers (locked) --
    def _q(self, sql, params=()):
        with self._lock:
            return self._con.execute(sql, params).fetchall()

    def _scalar(self, sql, params=()):
        return self._q(sql, params)[0][0]

    # -- retrieval --
    def _row_to_chunk(self, r) -> rag.Chunk:
        return rag.Chunk(
            chunk_id=r["chunk_id"], doc_id=r["doc_id"],
            title=r["title"] or "Untitled", subsection=r["subsection"] or "",
            date=r["date"] or "", source_url=r["url"] or "",
            page=r["page"], section=r["section"] or "",
            text=r["text"], kind=r["kind"])

    def _chunk_by_id(self, chunk_id: str):
        rows = self._q(
            """SELECT c.chunk_id, c.doc_id, d.title, d.subsection, d.date, d.url,
                      c.page, c.section, c.text, c.kind
               FROM chunks c JOIN documents d ON d.doc_id=c.doc_id
               WHERE c.chunk_id=?""", (chunk_id,))
        return rows[0] if rows else None

    def search(self, query: str, k: int = 6):
        with self._lock:
            rows = knowledge_kb.search(self._con, query, k=k)
        out = [(self._row_to_chunk(r), -float(r["score"])) for r in rows]  # +relevance
        if self.vindex is None:
            return out
        # semantic booster: append conceptual hits FTS missed (does not displace FTS)
        have = {c.chunk_id for c, _ in out}
        for cid, sim in self.vindex.search(query, k=k):
            if cid in have:
                continue
            r = self._chunk_by_id(cid)
            if r:
                out.append((self._row_to_chunk(r), float(sim)))
            if len(out) >= k + max(2, k // 2):
                break
        return out

    def ranked_docs(self, query: str, k: int = 8):
        best: dict[str, dict] = {}
        for c, s in self.search(query, k=48):
            cur = best.get(c.doc_id)
            if cur is None or s > cur["score"]:
                best[c.doc_id] = {"doc_id": c.doc_id, "title": c.title,
                                  "subsection": c.subsection, "score": round(s, 2),
                                  "page": c.page}
        return sorted(best.values(), key=lambda d: d["score"], reverse=True)[:k]

    # -- graph --
    def expand(self, doc_ids: list[str]) -> list[dict]:
        with self._lock:
            return knowledge_kb.expand(self._con, doc_ids)

    def neighbors(self, doc_id: str) -> dict:
        with self._lock:
            return knowledge_kb.neighbors(self._con, doc_id)

    def doc_status(self, doc_ids: list[str]) -> dict:
        with self._lock:
            return knowledge_kb.status_map(self._con, doc_ids)

    # -- memory layer (affinity + concepts); no-ops if memory tables absent --
    def _has_memory(self) -> bool:
        if knowledge_memory is None:
            return False
        with self._lock:
            return bool(self._con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='affinity'").fetchone())

    def related_by_theme(self, doc_ids: list[str], k: int = 6) -> list[dict]:
        if not self._has_memory():
            return []
        with self._lock:
            return knowledge_memory.related_by_affinity(self._con, doc_ids, k)

    def concepts(self, doc_ids: list[str], top: int = 8) -> list[dict]:
        if not self._has_memory():
            return []
        with self._lock:
            return knowledge_memory.concepts_for(self._con, doc_ids, top)

    def graph(self, focus: str | None = None, mode: str = "radial") -> dict:
        if not self._has_memory():
            return {"focus": None, "nodes": [], "edges": []}
        with self._lock:
            if focus and mode == "tree":
                return knowledge_memory.tree_subgraph(self._con, focus)
            if focus:
                return knowledge_memory.subgraph(self._con, focus)
            return knowledge_memory.hub_graph(self._con)

    def doclist(self, limit: int = 80) -> list[dict]:
        """Notable docs for the graph dropdown: hubs (PageRank) + master circulars."""
        if self._has_memory():
            rows = self._q(
                """SELECT d.doc_id, d.title, d.subsection FROM documents d
                   JOIN doc_metrics m ON m.doc_id=d.doc_id
                   WHERE d.title IS NOT NULL ORDER BY m.pagerank DESC LIMIT ?""", (limit,))
        else:
            rows = self._q("SELECT doc_id, title, subsection FROM documents "
                           "WHERE title IS NOT NULL ORDER BY doc_id LIMIT ?", (limit,))
        mc = self._q("SELECT doc_id, title, subsection FROM documents "
                     "WHERE lower(title) LIKE 'master circular%' ORDER BY date DESC LIMIT 20")
        seen, out = set(), []
        for r in list(rows) + list(mc):
            if r["doc_id"] in seen:
                continue
            seen.add(r["doc_id"])
            out.append({"doc_id": r["doc_id"], "title": r["title"], "subsection": r["subsection"]})
        return out

    def related(self, doc_ids: list[str], limit: int = 8) -> list[dict]:
        """One-hop related docs in BOTH directions, excluding the anchors."""
        if not doc_ids:
            return []
        qs = ",".join("?" * len(doc_ids))
        rows = self._q(
            f"""SELECT e.dst_doc doc_id, e.relation, 'cites' direction, e.src_doc anchor,
                       e.confidence, d.title, d.subsection, d.status
                FROM edges e JOIN documents d ON d.doc_id=e.dst_doc
                WHERE e.src_doc IN ({qs})
                UNION
                SELECT e.src_doc doc_id, e.relation, 'cited_by' direction, e.dst_doc anchor,
                       e.confidence, d.title, d.subsection, d.status
                FROM edges e JOIN documents d ON d.doc_id=e.src_doc
                WHERE e.dst_doc IN ({qs})
                ORDER BY confidence DESC""", (*doc_ids, *doc_ids))
        anchors = set(doc_ids)
        seen: set[str] = set()
        out = []
        for r in rows:
            d = dict(r)
            if d["doc_id"] in anchors or d["doc_id"] in seen:
                continue
            seen.add(d["doc_id"])
            out.append(d)
            if len(out) >= limit:
                break
        return out
