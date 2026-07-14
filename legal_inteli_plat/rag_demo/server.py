"""Local server for the agentic-RAG website — stdlib only.

Serves the site in ../frontend/ and exposes the agent API:

    ../preprocess/.venv/bin/python server.py            # http://127.0.0.1:8077
    ../preprocess/.venv/bin/python server.py --rebuild   # rebuild the index first

Endpoints:
    GET  /                 -> ../frontend/index.html
    GET  /meta             -> corpus stats + answer mode
    POST /api/ask          -> agentic turn {message, history:[{role,content}]}

Retrieval is BM25 over section-aware chunks (rag.py); the agent plans,
decomposes, clarifies, retrieves multi-hop and proposes follow-ups (agent.py).
Answers are extractive offline, generative if ANTHROPIC_API_KEY is set.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import agent as agent_mod
import rag

try:
    import kb_index
except Exception:  # knowledge deps missing
    kb_index = None

HERE = Path(__file__).resolve().parent
PARSED = HERE.parent / "preprocess" / "parsed"
FRONTEND = HERE.parent / "frontend"

AGENT: agent_mod.Agent


def _llm_ready() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:
        return False


def _load_index(rebuild: bool, hybrid: bool = False):
    """Prefer knowledge.db (FTS5 + reference graph); fall back to the BM25 pickle."""
    kdb = HERE.parent / "knowledge" / "knowledge.db"
    if kb_index is not None and kdb.exists() and not rebuild:
        try:
            idx = kb_index.KBIndex(kdb, use_vector=hybrid)
            extra = " + semantic booster" if idx.vindex is not None else ""
            print(f"backend: knowledge.db (FTS5 + graph, {idx.n_edges:,} edges{extra})")
            return idx
        except Exception as exc:
            print(f"knowledge.db load failed ({exc}); using pickle")
    cache = HERE / "index.pkl"
    if cache.exists() and not rebuild:
        print("backend: BM25 pickle")
        return rag.load_index(cache)
    idx = rag.build_index(PARSED)
    rag.save_index(idx, cache)
    print("backend: BM25 pickle (rebuilt)")
    return idx


def _doc_payload(doc_id: str, page: int | None = None) -> dict | None:
    """Read parsed/<doc_id>.json and return one page's real elements for the viz.

    Picks the most structurally varied page when none is given, and returns the
    page nav list so the frontend can move between pages of the same document.
    """
    if not doc_id:
        return None
    f = PARSED / f"{doc_id}.json"
    if not f.is_file():
        return None
    try:
        d = json.loads(f.read_text())
    except Exception:
        return None
    by_page: dict[int, list] = {}
    for e in d.get("elements", []):
        by_page.setdefault(e.get("page", 1), []).append(e)
    if not by_page:
        return None
    pages = []
    for pg, es in sorted(by_page.items()):
        types = sorted({x.get("type") for x in es})
        variety = len(types) + sum(1 for x in es if x.get("type") in ("table", "figure"))
        pages.append({"page": pg, "n": len(es), "types": types, "variety": variety})
    if page is None or page not in by_page:
        page = max(pages, key=lambda p: (p["variety"], p["n"]))["page"]
    els = sorted(by_page.get(page, []), key=lambda e: e.get("reading_order", 0))
    out, maxx, maxy = [], 0.0, 0.0
    for e in els:
        b = e.get("bbox") or {}
        x0, y0 = float(b.get("x0", 0)), float(b.get("y0", 0))
        x1, y1 = float(b.get("x1", 0)), float(b.get("y1", 0))
        maxx, maxy = max(maxx, x1), max(maxy, y1)
        tbl = e.get("table")
        if tbl:  # cap table for transport
            tbl = [[str(c)[:24] for c in row[:8]] for row in tbl[:12]]
        out.append({"o": e.get("reading_order", 0), "type": e.get("type"),
                    "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "text": (e.get("text") or "")[:200], "table": tbl})
    return {
        "doc_id": str(d.get("doc_id", doc_id)), "title": d.get("title") or "Untitled",
        "subsection": d.get("subsection") or "", "date": d.get("date") or "",
        "url": d.get("source_url") or "", "page_count": d.get("page_count"),
        "page": page,
        "pw": 595 if maxx <= 590 else round(maxx + 30),
        "ph": 842 if maxy <= 838 else round(maxy + 30),
        "pages": [{"page": p["page"], "n": p["n"], "types": p["types"]} for p in pages],
        "elements": out,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj) -> None:
        self._send(200, json.dumps(obj).encode(), "application/json")

    # ------------------------------------------------------------------ GET
    def do_GET(self) -> None:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        if path == "/meta":
            self._json({
                "docs": AGENT.n_docs, "passages": AGENT.n_passages,
                "edges": getattr(AGENT.index, "n_edges", 0),
                "mode": "claude" if _llm_ready() else "extractive",
                "subsections": AGENT.top_subsections,
            })
            return
        if path == "/api/search":
            q = (qs.get("q", [""])[0]).strip()
            self._json({"query": q, "docs": AGENT.index.ranked_docs(q) if q else []})
            return
        if path == "/api/doclist":
            self._json(AGENT.index.doclist() if hasattr(AGENT.index, "doclist") else [])
            return
        if path == "/api/graph":
            focus = (qs.get("doc", [""])[0]).strip() or None
            q = (qs.get("q", [""])[0]).strip()
            mode = (qs.get("mode", ["radial"])[0]).strip() or "radial"
            if not focus and q and hasattr(AGENT.index, "ranked_docs"):
                rd = AGENT.index.ranked_docs(q, 1)
                focus = rd[0]["doc_id"] if rd else None
            self._json(AGENT.index.graph(focus, mode) if hasattr(AGENT.index, "graph")
                       else {"focus": None, "nodes": [], "edges": []})
            return
        if path == "/api/doc":
            doc_id = (qs.get("id", [""])[0]).strip()
            page = qs.get("page", [None])[0]
            page = int(page) if page and page.isdigit() else None
            payload = _doc_payload(doc_id, page)
            if payload is None:
                self._send(404, b'{"error":"doc not found"}', "application/json")
            else:
                self._json(payload)
            return
        if path == "/" or path == "":
            path = "/index.html"
        target = (FRONTEND / path.lstrip("/")).resolve()
        if FRONTEND not in target.parents and target != FRONTEND / "index.html":
            self._send(403, b"forbidden", "text/plain")
            return
        if not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    # ----------------------------------------------------------------- POST
    def do_POST(self) -> None:
        if self.path != "/api/ask":
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            message = str(payload.get("message", ""))
            history = [agent_mod.Turn(t.get("role", "user"), t.get("content", ""))
                       for t in payload.get("history", []) if isinstance(t, dict)]
        except Exception:
            message, history = "", []
        try:
            self._json(AGENT.ask(message, history))
        except Exception as exc:  # never 500 the demo
            self._json({"type": "chat", "trace": [], "sources": [], "followups": [],
                        "answer": f"Agent error: {exc}"})


def main() -> None:
    global AGENT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8077)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--hybrid", action="store_true",
                    help="enable the model2vec semantic booster (needs knowledge/embeddings.npy)")
    args = ap.parse_args()

    print("loading index…")
    AGENT = agent_mod.Agent(_load_index(args.rebuild, args.hybrid))
    print(f"ready: {AGENT.n_passages:,} passages · {AGENT.n_docs:,} docs · "
          f"{'Claude' if _llm_ready() else 'extractive'} answers")
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"\n  ▶  http://{args.host}:{args.port}\n\n(Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
