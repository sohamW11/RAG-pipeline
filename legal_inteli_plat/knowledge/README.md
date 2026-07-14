# Knowledge base — SQLite + FTS5 + reference graph

The persistent store for the parsed SEBI corpus. Replaces the in-memory BM25
pickle with a single `knowledge.db` that holds retrieval **and** the relations
between documents. Stdlib only (Python `sqlite3` with FTS5).

## Build

```bash
cd legal_inteli_plat/knowledge
../preprocess/.venv/bin/python build.py                    # builds knowledge.db
../preprocess/.venv/bin/python build.py --demo "insider trading"
```

Rebuild anytime as more docs finish parsing (fast — ~3s for the whole corpus).

## What's inside `knowledge.db`

| table | purpose |
|-------|---------|
| `documents` | doc metadata + each doc's harvested `circular_no` |
| `chunks` | section-aware retrieval units |
| `chunks_fts` | FTS5 index — persistent BM25 search |
| `doc_refs` | raw citations extracted from text |
| `edges` | resolved doc→doc relations (typed) |

## Slice 1 — retrieval

`kb.search(con, query, k)` runs FTS5 `bm25()` over the chunks (lower score = more
relevant). Persistent and incrementally updatable, unlike the pickle.

## Slice 2 — the reference graph

Each SEBI circular prints its own ID near the top (`CIR/MRD/DP/21/2010`); we
harvest it, extract citations from the body (`references.py`), classify the
relation by the surrounding verb, and resolve the cited ID/date to a doc_id.

Relation types: **supersedes · amends · read_with · references**.

- `kb.neighbors(con, doc_id)` → incoming + outgoing edges for one doc.
- `kb.expand(con, doc_ids)` → one-hop related docs (graph expansion for retrieval).

### Current coverage (rebuild to refresh)
- ~1,295 docs · ~62k FTS chunks · ~4,200 citations found
- ~1,600 resolved edges · ~62% of docs connected
- e.g. the *Master Circular for Mutual Funds* supersedes the prior master
  circular and references the circulars it consolidates.

## Benchmark

`benchmark.py` uses data the corpus already provides — no hand labeling.

```bash
../preprocess/.venv/bin/python benchmark.py --n 300
```

Latest results (n=300):

**Task 1 — known-item** (query = title → find the doc)

| method | MRR | R@1 | R@5 | R@10 |
|--------|-----|-----|-----|------|
| FTS (lexical) | **0.613** | **0.473** | **0.790** | **0.867** |
| Semantic (model2vec) | 0.417 | 0.297 | 0.600 | 0.703 |
| Hybrid (RRF) | 0.523 | 0.387 | 0.690 | 0.817 |

**Task 2 — graph-expansion lift** (find the docs a doc cites)

| method | Recall@10 |
|--------|-----------|
| FTS only | 0.215 |
| **FTS + graph** | **0.861**  (**+64.6 pp**) |

The graph task is the headline: lexical search can't find cited circulars (they
don't share wording), but one-hop graph expansion quadruples recall — the payoff
of slice 2.

On known-item, **FTS wins** — a title shares exact words with its own text, the
ideal case for keyword search; static embeddings are a weaker signal. Semantic
retrieval only pulls ahead on **paraphrase** queries (e.g. "companies that
disappeared without informing shareholders" → finds *Vanishing Companies*, which
FTS misses). So the live default is FTS + graph; semantic is an **opt-in booster**
(`rag_demo/server.py --hybrid`, needs `embed.py` run first). A stronger neural
embedder + a paraphrase eval set would be the next refinement.

## Memory layer (correlation beyond citations)

`build_memory.py` (run after `build.py`) adds four tables — stdlib + numpy, no deps:

```bash
../preprocess/.venv/bin/python build_memory.py --linkpred
```

- `entities` / `doc_entities` — SEBI gazetteer (`entities.py`: AIF, InvIT, LODR,
  UPSI, …) + `Regulation N` / `Section N`. Fixes the acronym gap embeddings have.
- `affinity` — doc↔doc edges from three correlation signals, each normalised to
  [0,1] and pruned to top-20/doc: **co_citation** (cited by the same docs),
  **coupling** (citing the same docs), **entity** (sharing rare concepts, IDF-weighted).
- `doc_metrics` — **PageRank** centrality (finds the hub regs — ICDR 3152/2728 top
  out) + label-propagation **community**.

Built: 750 entities · ~30k affinity edges · 252 communities (~17s).

Query helpers (`memory.py`): `related_by_affinity()`, `concepts_for()`,
`entities_of()`. Wired into the agent via `KBIndex.related_by_theme()` +
`.concepts()` → the chat shows **Key concepts** chips and a **Related by theme**
panel (labelled by signal), alongside the citation-graph "Related documents".

### Link-prediction benchmark (do correlation signals predict citations?)

| signal | Recall@10 | MRR |
|--------|-----------|-----|
| co_citation | 0.135 | 0.097 |
| coupling | 0.066 | 0.071 |
| entity | 0.100 | 0.071 |
| **fused** | **0.178** | **0.127** |

Fused beats every single signal (they're complementary). The absolute number is
modest by design — correlation surfaces *thematically related* docs that are NOT
cited, which is the whole point (citations are sparse, 62% connected).

## Rebuild order
`build.py` (KB + graph + status) → `embed.py` (optional semantic) →
`build_memory.py` (entities + affinity + metrics).
