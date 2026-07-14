# Agentic RAG demo (Phase 3 preview)

Query the Phase-2 parsed SEBI corpus in natural language. Zero third-party
dependencies — pure-Python BM25 retrieval + stdlib HTTP server. The website
lives in `../frontend/`.

## Run

```bash
cd legal_inteli_plat/rag_demo
../preprocess/.venv/bin/python server.py            # http://127.0.0.1:8077
../preprocess/.venv/bin/python server.py --rebuild   # re-index after more docs finish parsing
```

Open http://127.0.0.1:8077

## Pieces

- `rag.py` — section-aware chunker + BM25 inverted index over `parsed/*.json`,
  cited retrieval. Build/query CLI: `python rag.py "your question"`.
- `agent.py` — the agentic layer: interprets follow-ups (coref), plans,
  decomposes multi-part questions, decides answer-vs-clarify, multi-hop
  retrieval, proposes follow-ups. Every step is recorded in a visible `trace`.
- `server.py` — serves `../frontend/` and the `/api/ask` + `/meta` endpoints.
- `index.pkl` — cached index (rebuild with `--rebuild`).

## Answer modes

- **extractive** (default, offline): stitches the highest query-overlap
  sentences from retrieved passages, each cited to `doc_id · page · title`.
- **generative** (automatic upgrade): set `ANTHROPIC_API_KEY` and
  `pip install anthropic`; the agent then asks Claude (`claude-opus-4-8`) to
  write a grounded answer over the *same* retrieved context. Retrieval and
  citations are identical — only the prose synthesis changes.

The corpus is still being parsed; re-run with `--rebuild` to fold in new docs.
