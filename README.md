# RAG-pipeline — SEBI Legal Intelligence Platform

An end-to-end retrieval-augmented pipeline over **SEBI**'s (Securities and Exchange
Board of India) full legal corpus — circulars, regulations, master circulars,
acts, rules and notifications. It crawls the source, turns every PDF into
provenance-tagged structured elements, builds a searchable knowledge base with a
document **citation graph** and a **correlation memory layer**, and serves an
**agentic RAG** assistant plus interactive graph visualizations.

Everything runs locally with a near-zero dependency footprint (stdlib + numpy for
the knowledge/serving layers), by design — the corpus is large and the deploy
target is offline-friendly.

---

## Status at a glance

| Phase | Component | Dir | State |
|------:|-----------|-----|-------|
| 1 | **Crawler** — scrape & download SEBI PDFs + metadata | [`legal_inteli_plat/crawler/`](legal_inteli_plat/crawler/) | ✅ Done — 3,502 PDFs + `crawler.db` |
| 2 | **Preprocessing** — PDF → normalized structured elements | [`legal_inteli_plat/preprocess/`](legal_inteli_plat/preprocess/) | ✅ Code done · corpus run in progress |
| 3 | **Knowledge base** — SQLite/FTS5 + citation graph + memory layer | [`legal_inteli_plat/knowledge/`](legal_inteli_plat/knowledge/) | ✅ Done |
| 3 | **Agentic RAG** — retrieval + agent + chat UI | [`legal_inteli_plat/rag_demo/`](legal_inteli_plat/rag_demo/) + [`frontend/`](legal_inteli_plat/frontend/) | ✅ Done (demo) |
| 4 | Fact extraction / obligations · productionization | `parser/`, `api/`, `infra/` | ⬜ Planned |

```
   Phase 1                Phase 2                     Phase 3
 ┌──────────┐   PDFs   ┌───────────────┐  parsed/  ┌──────────────────────────┐
 │ crawler  │─────────▶│ preprocess    │──────────▶│ knowledge base (SQLite)  │
 │ (SEBI)   │  +meta   │ triage→docling│ {doc}.json│  FTS5 · citation graph   │
 └──────────┘  (.db)   │ →OCR→normalize│           │  status · memory/affinity│
                       └───────────────┘           └────────────┬─────────────┘
                                                                │
                                        agentic RAG  ┌──────────▼───────────┐
                                        + graph viz  │ rag_demo/ + frontend/│
                                                     └──────────────────────┘
```

---

## Phase 1 — Crawler ✅

Scrapes SEBI's AJAX/iframe-driven site (session-fragile pagination, iframe-wrapped
PDFs) and downloads **3,502 PDFs** across 12 sections (Circulars ~2,782,
Regulations ~1,109 dominate), with full metadata in `crawler.db`
(`document_number`, title, dates, department, category, URLs, keywords).

```bash
cd legal_inteli_plat && python -m crawler crawl        # see crawler/ for CLI
```

## Phase 2 — Preprocessing ✅ (corpus run in progress)

Turns each PDF into a list of **normalized, provenance-tagged elements**
(headings, paragraphs, lists, tables, figures) with an identical output shape
whether the source was native or scanned. Spec: [`legal_inteli_plat/claude.md`](legal_inteli_plat/claude.md).

- **PyMuPDF** triages native vs scanned *per page*; **Docling** parses native
  pages (text + layout + reading order + tables in one pass); **RapidOCR** handles
  scanned pages; **Camelot** repairs broken native tables only.
- Output: `parsed/{doc_id}.json` (validated pydantic) + a run manifest. Idempotent,
  resumable, one bad PDF never aborts the batch.
- Sharded corpus runner + crash-proof supervisor; progress via
  [`preprocess/scripts/check_progress.sh`](legal_inteli_plat/preprocess/scripts/check_progress.sh).

```bash
cd legal_inteli_plat/preprocess
./.venv/bin/python -m sebi_preprocessing preprocess <dir|pdf> [--limit N] [--force]
bash scripts/check_progress.sh          # monitor the sharded corpus run
```

## Phase 3 — Knowledge base + agentic RAG ✅

### Knowledge base ([`knowledge/`](legal_inteli_plat/knowledge/))
A single `knowledge.db` (SQLite, stdlib only) that holds retrieval **and** the
relationships between documents.

- **Retrieval** — section-aware chunks indexed with **FTS5 `bm25()`** (persistent,
  incremental; replaces an in-memory pickle).
- **Citation graph** — each doc's own circular ID is harvested; citations are
  extracted from the text and resolved to typed edges: `supersedes`, `amends`,
  `read_with`, `references`, `consolidates`.
- **Currency / status** — every doc is marked `live` / `superseded` / `amended`
  with a pointer to the live replacement (a temporal guard drops backward-dated
  noise) so **stale law is never surfaced as current** — a legal-tool correctness
  requirement.
- **Memory layer** — correlation beyond citations: an **entity/concept** layer
  (SEBI gazetteer + `Regulation N` / `Section N`), a unified **affinity** graph
  (co-citation + bibliographic coupling + entity overlap), and **PageRank** +
  **community** metrics.

```bash
cd legal_inteli_plat/knowledge
../preprocess/.venv/bin/python build.py            # KB + citation graph + status
../preprocess/.venv/bin/python embed.py            # (optional) semantic vectors
../preprocess/.venv/bin/python build_memory.py     # entities + affinity + metrics
../preprocess/.venv/bin/python benchmark.py        # retrieval metrics
```

### Agentic RAG ([`rag_demo/`](legal_inteli_plat/rag_demo/) + [`frontend/`](legal_inteli_plat/frontend/))
An agent that plans, decomposes multi-part questions, asks clarifying follow-ups,
retrieves (with graph expansion), flags superseded sources, and cites every claim
to `doc_id · page · section`. Answers are **extractive by default**, auto-upgrading
to **Claude** (`claude-opus-4-8`) if `ANTHROPIC_API_KEY` is set.

```bash
cd legal_inteli_plat/rag_demo
python server.py            # → http://127.0.0.1:8077   (chat)
python server.py --hybrid   # + semantic booster
```

Web surfaces (served by the same server):
- **`/`** — agentic chat with reasoning trace, cited sources (currency-badged),
  related documents (citation + thematic), and key concepts.
- **`/graph.html`** — interactive knowledge-graph: hierarchical **tree** or radial
  view of a document's citation + supersession + affinity neighborhood.
- **`/layout.html`** — animated walkthrough of the Phase-2 layout-understanding
  pipeline.

---

## Benchmarks (real, on the current corpus)

Grounded in data the corpus already provides — no hand labeling
([`knowledge/benchmark.py`](legal_inteli_plat/knowledge/benchmark.py),
`build_memory.py --linkpred`).

| Task | Metric | Result |
|------|--------|--------|
| Known-item retrieval (title → doc) | MRR / Recall@10 | **0.61 / 0.87** (FTS wins vs semantic 0.42, hybrid 0.52) |
| Graph-expansion lift (find cited docs) | Recall@10 FTS vs FTS+graph | 0.215 → **0.861** (**+64.6 pp**) |
| Memory link-prediction (predict citations) | Recall@10 (fused) | **0.178** (fused > co-citation 0.135 > entity 0.100 > coupling 0.066) |

**Takeaways:** for a legal corpus, mine the *explicit* structure first — lexical
FTS beats generic embeddings on exact identifiers, and the **citation graph is the
biggest win** (+64.6 pp). Semantic embeddings help only on paraphrase queries;
they're an opt-in booster, not the default.

---

## Repository layout

```
legal_inteli_plat/
  crawler/            Phase 1 — SEBI crawler (FastAPI + CLI)
  preprocess/         Phase 2 — sebi_preprocessing package (triage/docling/ocr/tables)
  knowledge/          Phase 3 — knowledge.db builder + graph + memory + benchmarks
  rag_demo/           Phase 3 — agentic RAG server (stdlib http + BM25/graph retrieval)
  frontend/           Phase 3 — chat + knowledge-graph + pipeline visualizations
  parser/ graph/ api/ infra/   scaffolds for Phase 4
  crawler.db          Phase-1 metadata (gitignored)
  claude.md           Phase-2 spec
```

Branches follow the phases: `main` (default), `crawler`, `preprocess`, and the
Phase-3 `knowledge`/RAG branch. Generated artifacts (`*.db`, `*.npy`, `*.pkl`,
`parsed/`, `.venv/`) are gitignored and rebuilt by the scripts above.

---

## Roadmap — what's next

1. **Finish the Phase-2 corpus run** (3,502 docs) and rebuild the KB + memory to
   cover the full corpus (`build.py → embed.py → build_memory.py`).
2. **Evaluation set** — 30–50 hand-labeled question/answer pairs with a *currency*
   dimension (is the top result live law?) to measure answer-relevance, not just
   relatedness.
3. **Identifier-aware FTS** — route `Regulation N` / circular numbers to phrase/exact
   matching instead of OR-of-terms.
4. **Better embeddings** — evaluate `bge-small` (fastembed) vs the current
   `model2vec`, only if the eval shows concepts are the retrieval bottleneck.
5. **Phase 4 — fact/obligation extraction** — structured obligations per document
   (who must do what, by when) on top of the parsed elements and the graph.
6. **Productionization** — API service (`api/`), storage wiring, and deploy infra
   (`infra/`).

> Demo/research code over public regulatory documents — **not legal advice**.
