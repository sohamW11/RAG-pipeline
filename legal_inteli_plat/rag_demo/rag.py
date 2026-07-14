"""Phase-3 demo RAG core over the Phase-2 parsed corpus.

Zero third-party dependencies (stdlib only) so it runs anywhere the parsed JSON
lives. Pipeline:

    parsed/{doc_id}.json  ->  section-aware chunks (provenance-tagged)
                          ->  pure-Python BM25 inverted index (pickled)
                          ->  retrieve top-k  ->  extractive answer + citations

The generative layer is intentionally pluggable: if ANTHROPIC_API_KEY is set and
the `anthropic` SDK is importable, `answer()` asks Claude to write a grounded
answer over the retrieved context; otherwise it falls back to an extractive
answer stitched from the best-matching sentences. Retrieval is identical either
way, so the demo is honest about where each sentence came from.
"""

from __future__ import annotations

import json
import math
import os
import pickle
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- tokenisation

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "by", "with",
    "as", "at", "is", "are", "be", "been", "was", "were", "this", "that", "these",
    "those", "it", "its", "from", "shall", "may", "any", "all", "such", "which",
    "under", "sub", "section", "sebi", "no", "not", "has", "have", "will", "into",
    "per", "date", "dated", "sir", "form",
}
_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    toks = _TOKEN.findall(text.lower())
    return [t for t in toks if len(t) > 1 and t not in _STOP]


_SENT = re.compile(r"(?<=[.;:])\s+(?=[A-Z0-9(])")


def split_sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENT.split(text) if s.strip()]
    return parts or ([text.strip()] if text.strip() else [])


# ---------------------------------------------------------------- data classes


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    subsection: str
    date: str
    source_url: str
    page: int
    section: str          # nearest heading
    text: str
    kind: str = "text"    # "text" | "table"


@dataclass
class RagIndex:
    chunks: list[Chunk]
    postings: dict[str, list[tuple[int, int]]]   # term -> [(chunk_idx, tf)]
    idf: dict[str, float]
    doc_len: list[int]
    avgdl: float
    k1: float = 1.5
    b: float = 0.75

    # -------------------------------------------------------------- retrieval
    def search(self, query: str, k: int = 6) -> list[tuple[Chunk, float]]:
        q_terms = tokenize(query)
        if not q_terms:
            return []
        scores: dict[int, float] = defaultdict(float)
        for term in set(q_terms):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for idx, tf in self.postings.get(term, ()):
                dl = self.doc_len[idx]
                denom = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                scores[idx] += idf * (tf * (self.k1 + 1)) / denom
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]
        return [(self.chunks[i], s) for i, s in ranked]

    # ---------------------------------------------------- ranked docs (viz)
    def ranked_docs(self, query: str, k: int = 8) -> list[dict]:
        """Distinct documents relevant to the query, each with its best page."""
        best: dict[str, dict] = {}
        for c, s in self.search(query, k=48):
            cur = best.get(c.doc_id)
            if cur is None or s > cur["score"]:
                best[c.doc_id] = {"doc_id": c.doc_id, "title": c.title,
                                  "subsection": c.subsection, "score": round(s, 2),
                                  "page": c.page}
        return sorted(best.values(), key=lambda d: d["score"], reverse=True)[:k]

    # -------------------------------------------------------------- answering
    def answer(self, query: str, k: int = 6) -> dict:
        hits = self.search(query, k=k)
        if not hits:
            return {
                "answer": "No passage in the parsed corpus matched that query. "
                          "Try different or more specific terms.",
                "mode": "empty",
                "sources": [],
            }
        sources = [_source_dict(c, s) for c, s in hits]
        llm = _try_llm_answer(query, hits)
        if llm is not None:
            return {"answer": llm, "mode": "generative", "sources": sources}
        return {"answer": _extractive_answer(query, hits), "mode": "extractive",
                "sources": sources}


def _source_dict(c: Chunk, score: float) -> dict:
    snippet = c.text if len(c.text) <= 320 else c.text[:317] + "…"
    return {
        "doc_id": c.doc_id, "title": c.title, "subsection": c.subsection,
        "date": c.date, "url": c.source_url, "page": c.page,
        "section": c.section, "kind": c.kind, "score": round(score, 2),
        "snippet": snippet,
    }


def _extractive_answer(query: str, hits: list[tuple[Chunk, float]]) -> str:
    """Stitch the highest query-overlap sentences from the top chunks, cited."""
    q = set(tokenize(query))
    scored: list[tuple[float, str, Chunk]] = []
    for c, _ in hits[:4]:
        for sent in split_sentences(c.text):
            st = set(tokenize(sent))
            if not st:
                continue
            overlap = len(q & st)
            if overlap == 0:
                continue
            scored.append((overlap / (1 + math.log1p(len(st))), sent, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    lines: list[str] = []
    for _, sent, c in scored:
        key = sent[:60].lower()
        if key in seen:
            continue
        seen.add(key)
        cite = f"[{c.doc_id} · p.{c.page} · {c.title[:40]}]"
        lines.append(f"{sent} {cite}")
        if len(lines) >= 4:
            break
    if not lines:
        c = hits[0][0]
        return f"{c.text[:400]} [{c.doc_id} · p.{c.page} · {c.title[:40]}]"
    return "\n\n".join(lines)


def _try_llm_answer(query: str, hits: list[tuple[Chunk, float]]) -> str | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  # type: ignore
    except Exception:
        return None
    ctx = "\n\n".join(
        f"[source {i+1} | doc {c.doc_id} | {c.title} | p.{c.page} | {c.section}]\n{c.text}"
        for i, (c, _) in enumerate(hits)
    )
    prompt = (
        "You are a SEBI regulatory assistant. Answer the question using ONLY the "
        "sources below. Cite sources inline as [doc_id p.N]. If the sources do not "
        "contain the answer, say so.\n\n"
        f"SOURCES:\n{ctx}\n\nQUESTION: {query}\n\nGrounded answer:"
    )
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-opus-4-8", max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    except Exception:
        return None


# ---------------------------------------------------------------- index build

_MAX_CHARS = 900


def _flatten_table(rows: list[list[str]]) -> str:
    out = []
    for r in rows[:12]:
        cells = [str(c).strip() for c in r if str(c).strip()]
        if cells:
            out.append(" | ".join(cells))
    return "  ;  ".join(out)


def _iter_chunks(doc: dict) -> list[Chunk]:
    meta = dict(
        doc_id=str(doc.get("doc_id", "?")),
        title=(doc.get("title") or "Untitled").strip(),
        subsection=doc.get("subsection") or "",
        date=doc.get("date") or "",
        source_url=doc.get("source_url") or "",
    )
    els = sorted(
        doc.get("elements", []),
        key=lambda e: (e.get("part", 0), e.get("page", 0), e.get("reading_order", 0)),
    )
    collected: list[Chunk] = []
    state = {"section": "", "buf": [], "page": 1, "n": 0}

    def flush() -> None:
        text = " ".join(state["buf"]).strip()
        state["buf"] = []
        if len(text) < 25:
            return
        for i in range(0, len(text), _MAX_CHARS):
            collected.append(Chunk(
                chunk_id=f"{meta['doc_id']}::c{state['n']}", page=state["page"],
                section=state["section"], text=text[i:i + _MAX_CHARS],
                kind="text", **meta,
            ))
            state["n"] += 1

    for e in els:
        state["page"] = e.get("page", state["page"])
        etype = e.get("type")
        if etype == "heading":
            flush()
            state["section"] = (e.get("text") or "").strip()[:120]
            if state["section"]:
                state["buf"].append(state["section"])
        elif etype == "table" and e.get("table"):
            flush()
            collected.append(Chunk(
                chunk_id=f"{meta['doc_id']}::t{state['n']}", page=state["page"],
                section=state["section"],
                text=f"[table] {_flatten_table(e['table'])}", kind="table", **meta,
            ))
            state["n"] += 1
        elif e.get("text"):
            state["buf"].append(e["text"].strip())
            if sum(len(x) for x in state["buf"]) >= _MAX_CHARS:
                flush()
    flush()
    return collected


def build_index(parsed_dir: str | Path) -> RagIndex:
    parsed_dir = Path(parsed_dir)
    files = [f for f in parsed_dir.glob("*.json") if "manifest" not in f.name]
    chunks: list[Chunk] = []
    for f in files:
        try:
            doc = json.loads(f.read_text())
        except Exception:
            continue
        chunks.extend(_iter_chunks(doc))

    postings: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    doc_len: list[int] = []
    for i, c in enumerate(chunks):
        toks = tokenize(c.text)
        doc_len.append(len(toks))
        for t in toks:
            postings[t][i] += 1

    n_docs = max(len(chunks), 1)
    idf: dict[str, float] = {}
    flat_postings: dict[str, list[tuple[int, int]]] = {}
    for term, d in postings.items():
        df = len(d)
        idf[term] = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
        flat_postings[term] = list(d.items())
    avgdl = (sum(doc_len) / n_docs) if n_docs else 1.0
    return RagIndex(chunks=chunks, postings=flat_postings, idf=idf,
                    doc_len=doc_len, avgdl=avgdl)


_DEFAULT_CACHE = Path(__file__).with_name("index.pkl")


def save_index(idx: RagIndex, path: str | Path = _DEFAULT_CACHE) -> None:
    Path(path).write_bytes(pickle.dumps(idx))


def load_index(path: str | Path = _DEFAULT_CACHE) -> RagIndex:
    return pickle.loads(Path(path).read_bytes())


if __name__ == "__main__":
    import sys
    here = Path(__file__).resolve().parent
    parsed = here.parent / "preprocess" / "parsed"
    print(f"building index from {parsed} ...")
    idx = build_index(parsed)
    save_index(idx)
    print(f"indexed {len(idx.chunks):,} chunks from parsed docs -> {_DEFAULT_CACHE}")
    if len(sys.argv) > 1:
        q = " ".join(sys.argv[1:])
        res = idx.answer(q)
        print(f"\nQ: {q}\nMODE: {res['mode']}\n\n{res['answer']}\n")
        for s in res["sources"]:
            print(f"  - [{s['score']}] {s['doc_id']} p.{s['page']} :: {s['title'][:50]}")
