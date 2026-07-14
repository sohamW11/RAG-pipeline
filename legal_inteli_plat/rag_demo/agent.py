"""Agentic RAG layer over the BM25 retriever (rag.py).

This is more than one-shot retrieve-then-answer. Each turn the agent:

  1. INTERPRETS the message in the context of the conversation (resolves
     follow-ups like "tell me more" against the previous topic).
  2. PLANS — decides whether to answer, or to ask a clarifying question when the
     request is too broad / ambiguous, and decomposes multi-part questions
     ("compare X and Y") into separate retrievals.
  3. RETRIEVES per sub-query and merges the evidence (multi-hop).
  4. SYNTHESISES a grounded, cited answer (extractive offline; Claude if a key
     is present — identical retrieval either way).
  5. PROPOSES follow-up questions derived from the retrieved sections, so the
     conversation can keep going.

Every step is recorded in a `trace` the UI renders, so the agent's reasoning is
visible rather than a black box. Runs fully offline; upgrades automatically when
ANTHROPIC_API_KEY + the `anthropic` SDK are available.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass

import rag

_FOLLOWUP_HINTS = {"more", "detail", "details", "explain", "elaborate", "continue",
                   "further", "that", "this", "it", "those", "them", "again"}
_GREETING = re.compile(r"^\s*(hi|hey|hello|thanks|thank you|ok|okay|cool|nice)\b", re.I)
_SPLIT = re.compile(r"\bcompared?\b|\bvs\.?\b|\bversus\b|\bdifference between\b|\band\b", re.I)

# generic next-step questions that are useful across SEBI legal material
_INTENT_TEMPLATES = [
    ("penal", "What are the penalties or consequences?"),
    ("time", "What are the timelines or deadlines?"),
    ("appl", "Who does this apply to?"),
    ("defin", "How are the key terms defined?"),
    ("proced", "What is the procedure to follow?"),
]


@dataclass
class Turn:
    role: str
    content: str


class Agent:
    def __init__(self, index):
        self.index = index
        # works with both rag.RagIndex (has .chunks) and KBIndex (has stats attrs)
        self.n_passages = getattr(index, "n_passages", None) or len(index.chunks)
        self.n_docs = getattr(index, "n_docs", None) or len({c.doc_id for c in index.chunks})
        if getattr(index, "top_subsections", None):
            self.top_subsections = index.top_subsections
        else:
            counts = collections.Counter(c.subsection for c in index.chunks if c.subsection)
            self.top_subsections = [s for s, _ in counts.most_common(6)]

    # --------------------------------------------------------------- planning
    def _resolve(self, message: str, history: list[Turn]) -> tuple[str, list[str]]:
        """Resolve a terse follow-up against the last user question."""
        trace: list[str] = []
        content = rag.tokenize(message)
        prev = next((t.content for t in reversed(history) if t.role == "user"), None)
        is_followup = prev and (len(content) <= 2 or
                                _FOLLOWUP_HINTS.intersection(message.lower().split()))
        if is_followup and prev:
            merged = f"{prev} {message}"
            trace.append(f"Read as a follow-up to “{prev[:60]}” → searching “{merged[:70]}”")
            return merged, trace
        return message, trace

    def _decompose(self, query: str) -> list[str]:
        low = query.lower()
        if any(k in low for k in ("compare", " vs", "versus", "difference between")):
            parts = [p.strip(" ,.?") for p in _SPLIT.split(query)]
            subs = [p for p in parts if len(rag.tokenize(p)) >= 1]
            if len(subs) >= 2:
                return subs[:3]
        return [query]

    def _needs_clarify(self, query: str, hits) -> bool:
        content = rag.tokenize(query)
        if len(content) <= 1:
            return True
        if hits:
            subs = {c.subsection for c, _ in hits[:6] if c.subsection}
            top = hits[0][1]
            close = sum(1 for _, s in hits[:5] if s >= 0.6 * top)
            # broad term hitting many document families with no clear winner
            if len(subs) >= 4 and close >= 4:
                return True
        return False

    # ------------------------------------------------------------ retrieval
    def _multi_retrieve(self, subqueries: list[str], k: int):
        merged: dict[str, tuple] = {}
        for sq in subqueries:
            for c, s in self.index.search(sq, k=k):
                cur = merged.get(c.chunk_id)
                if cur is None or s > cur[1]:
                    merged[c.chunk_id] = (c, s)
        return sorted(merged.values(), key=lambda x: x[1], reverse=True)

    # ----------------------------------------------------------- follow-ups
    def _followups(self, query: str, hits) -> list[str]:
        out: list[str] = []
        seen_titles: set[str] = set()
        for c, _ in hits:
            if c.title and c.title not in seen_titles and len(out) < 2:
                seen_titles.add(c.title)
                out.append(f"What else does “{c.title[:44]}” cover?")
        low = query.lower()
        for key, q in _INTENT_TEMPLATES:
            if key not in low and len(out) < 4:
                out.append(q)
        return out[:4]

    # --------------------------------------------------------------- ask()
    def ask(self, message: str, history: list[Turn] | None = None) -> dict:
        history = history or []
        message = (message or "").strip()
        if not message:
            return self._chat("Ask me anything about the SEBI corpus.", history)
        if _GREETING.match(message) and len(rag.tokenize(message)) <= 1:
            return self._chat(
                f"Hi — I can search {self.n_docs:,} SEBI documents "
                f"({self.n_passages:,} passages). Ask about a circular, regulation, "
                "obligation, or filing requirement.", history)

        trace: list[str] = []
        query, rtrace = self._resolve(message, history)
        trace += rtrace

        subs = self._decompose(query)
        if len(subs) > 1:
            trace.append(f"Decomposed into {len(subs)} sub-questions: "
                         + " | ".join(s[:40] for s in subs))
        else:
            trace.append("Planned a single retrieval")

        hits = self._multi_retrieve(subs, k=6)
        trace.append(f"Searched {self.n_passages:,} passages → {len(hits)} candidates "
                     f"across {len({c.doc_id for c,_ in hits})} documents")

        if not hits:
            return {
                "type": "clarify",
                "trace": trace + ["No matches — asking user to rephrase"],
                "answer": "I couldn't find anything on that in the parsed corpus. "
                          "Could you rephrase, or name a specific instrument or topic?",
                "options": [f"Show me {s}" for s in self.top_subsections[:4]],
                "sources": [], "followups": [],
            }

        if self._needs_clarify(query, hits):
            fams = []
            for c, _ in hits:
                if c.subsection and c.subsection not in fams:
                    fams.append(c.subsection)
            fams = fams[:4] or self.top_subsections[:4]
            trace.append("Query is broad — asking a clarifying question")
            return {
                "type": "clarify",
                "trace": trace,
                "answer": f"That spans a few areas. Which should I focus on for "
                          f"“{query}”?",
                "options": [f"{query} — in {f}" for f in fams],
                "sources": [rag._source_dict(c, s) for c, s in hits[:3]],
                "followups": [],
            }

        # graph expansion: related documents (master circular + amendments etc.)
        related = self._graph_expand(hits, trace)
        sources = [rag._source_dict(c, s) for c, s in hits]
        self._annotate_status(sources, trace)

        # memory layer: unified fused relatedness (citation + affinity) + key concepts
        top_docs = list(dict.fromkeys(c.doc_id for c, _ in hits))[:5]
        if hasattr(self.index, "related_fused"):
            related_theme = self.index.related_fused(top_docs, 8)
        elif hasattr(self.index, "related_by_theme"):
            related_theme = self.index.related_by_theme(top_docs, 8)
        else:
            related_theme = []
        concepts = self.index.concepts(top_docs) if hasattr(self.index, "concepts") else []
        if related_theme or concepts:
            bits = []
            if related_theme:
                bits.append(f"{len(related_theme)} related docs (fused)")
            if concepts:
                bits.append("key concepts: " + ", ".join(c["name"] for c in concepts[:4]))
            trace.append("Memory layer: " + " · ".join(bits))

        # temporal version threads: answer from the in-force version, show history
        timelines = self.index.thread_info(top_docs) if hasattr(self.index, "thread_info") else []
        if timelines:
            trace.append("Version threads: " + str(len(timelines)) + " timeline(s) — answering from "
                         "the in-force version, older ones shown as history")

        trace.append(f"Synthesising a grounded answer from the top "
                     f"{min(4, len(hits))} passages")
        llm = rag._try_llm_answer(query, hits)
        answer = llm if llm is not None else rag._extractive_answer(query, hits)
        return {
            "type": "answer",
            "mode": "generative" if llm is not None else "extractive",
            "trace": trace,
            "answer": answer,
            "sources": sources,
            "related": related,
            "related_theme": related_theme,
            "concepts": concepts,
            "timelines": timelines,
            "followups": self._followups(query, hits),
        }

    def _annotate_status(self, sources: list[dict], trace: list[str]) -> None:
        """Tag each source with currency status; flag superseded law loudly."""
        if not hasattr(self.index, "doc_status"):
            return
        sm = self.index.doc_status(list({s["doc_id"] for s in sources}))
        stale = 0
        for s in sources:
            info = sm.get(s["doc_id"], {})
            s["status"] = info.get("status", "live")
            s["superseded_by"] = info.get("superseded_by")
            s["superseded_by_title"] = info.get("superseded_by_title")
            s["superseded_by_date"] = info.get("superseded_by_date")
            if s["status"] == "superseded":
                stale += 1
        if stale:
            trace.append(f"Currency check: {stale} retrieved doc(s) superseded — "
                         "flagged as stale, live version linked")

    def _graph_expand(self, hits, trace: list[str]) -> list[dict]:
        """Pull one-hop related docs via the reference graph, if available."""
        if not hasattr(self.index, "related"):
            return []
        top_docs = []
        for c, _ in hits:
            if c.doc_id not in top_docs:
                top_docs.append(c.doc_id)
            if len(top_docs) >= 5:
                break
        rel = self.index.related(top_docs, limit=6)
        if rel:
            kinds = collections.Counter(r["relation"] for r in rel)
            trace.append("Graph expansion: " + str(len(rel)) + " related documents ("
                         + ", ".join(f"{n} {k}" for k, n in kinds.most_common()) + ")")
        return [{"doc_id": r["doc_id"], "title": r["title"], "relation": r["relation"],
                 "direction": r["direction"], "subsection": r.get("subsection"),
                 "status": r.get("status", "live")} for r in rel]

    def _chat(self, text: str, history) -> dict:
        return {"type": "chat", "trace": [], "answer": text,
                "sources": [], "followups": [
                    "What are the criteria for a vanishing company?",
                    "Disclosure requirements for mutual funds",
                    "Obligations of bankers to an issue"]}
