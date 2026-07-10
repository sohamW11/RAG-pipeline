# SEBI Preprocessing (Phase 2)

Turns raw SEBI PDFs into normalized, provenance-tagged structured elements
(headings, paragraphs, tables) with an identical output shape regardless of
whether the source page was native or scanned. See `../claude.md` for the full
spec; this README covers setup and the Checkpoint-0 recon findings.

## Flow (do not reorder)
PyMuPDF triages native vs scanned **per page** → native pages go through Docling
(text + reading order + tables) → scanned pages go through the OCR adapter
(English for now) → any *broken* table is repaired (Camelot for native, VLM for
scanned) → everything is normalized into one tagged `DocumentElement` schema.

## Setup
```bash
cd legal_inteli_plat/preprocess
python3.12 -m venv .venv
./.venv/bin/pip install -e .            # or: pip install -r requirements.lock.txt
```
Requires **Ghostscript** on the host for Camelot (`gs --version`; 10.02.1 here).

Run recon scripts (Checkpoint 0):
```bash
./.venv/bin/python scripts/recon_triage.py 100          # native/scanned threshold sweep
./.venv/bin/python scripts/recon_tables_mixed.py 12      # mixed-page + table-rich detection
./.venv/bin/python scripts/recon_docling.py  tests/fixtures/native_table_83899.pdf
./.venv/bin/python scripts/recon_camelot.py  tests/fixtures/native_table_83899.pdf all
```

## Fixtures (`tests/fixtures/`) — real SEBI docs
| file | doc_id | pages | character |
|------|--------|-------|-----------|
| `native_text_13565.pdf`   | 13565 | 1 | native, text-only (no tables) |
| `native_table_83899.pdf`  | 83899 | 3 | native, **real ruled table** (abbreviations, p2) + a tabular text block (p3) — sliced from the 42-page Bankers-to-an-Issue master circular |
| `native_table_34658.pdf`  | 34658 | 4 | native legal text that *looks* tabular but has **no real tables** (Docling agrees: 0 tables). Gate stress-test / false-positive guard |
| `scanned_25769.pdf`       | 25769 | 3 | fully scanned (image-only, 0 extractable chars) |
| `mixed_101817.pdf`        | 101817| 5 | **mixed**: 4 native pages + 1 scanned page (index 3, 71 chars) — sliced from the 153-page AIF master circular |

## Checkpoint-0 recon findings

### 1. Triage threshold (PyMuPDF text length)
The native/scanned split is **clean and wide**. Across a 106-PDF sample, scanned
pages carry **0–71** extractable chars while native pages carry **186–5000+**
(median ~1000–3000). There is no ambiguous middle band.
- **`native_char_threshold: 100`** separates every sampled page correctly. Even
  10–50 would work; 100 is a safe margin. (One real scanned page had a 71-char
  text stamp — 100 still classifies it scanned.)
- **Mixed-page PDFs are real**: `master-circular-...aifs_101817` (153pp) has a
  single scanned page (146) inside an otherwise-native document. **Per-page
  triage is mandatory**, exactly as specified. Fully-scanned docs also exist
  (old Acts, some Regulations/Rules).

### 2. Docling on a native+table page
On `native_table_83899.pdf` Docling emits a clean element stream with **bboxes on
every element** and correct reading order, labeling content as
`section_header`, `text`, `list_item`, `footnote`, `page_footer`. The ruled
abbreviations table comes out as a **GOOD table**: `17x2, empty=0%, ragged=False,
max_single_cell_text_share=14%` — passes every gate heuristic.

A **BROKEN** table (for the §5 gate) would fail one of: `<2` rows/cols, `>40%`
empty cells, ragged row lengths, or one cell hoarding the text (collapsed
columns). Those thresholds now live in `config.yaml::table_gate`.

Docling is genuinely smart about *not* inventing tables: on `native_table_34658`
(legal text with marginal-heading indentation) PyMuPDF `find_tables()` reported
24 "tables" and Camelot-stream reported 4 — **all false positives**. Docling
reported **0 tables** and read the content as headings/list-items/paragraphs.
This is why the spec routes the whole native page through Docling, not a
table-hunting heuristic.

Docling label → schema `type` mapping (to implement in `normalize.py`):
`section_header→heading`, `text/footnote→paragraph`, `list_item→list`,
`table→table`, `picture→figure`, `caption→caption`,
`page_header/page_footer→header_footer`.

### 3. Camelot on the native sample — confirmed, and how it differs
Camelot runs (Ghostscript present). On the **same** ruled table (bankers p5):
- `flavor=lattice` → **18x2, accuracy=100%, whitespace=2.8%** — matches Docling's
  17x2 (header handling differs by one row). Lattice reads vector ruling lines.
- On `34658` (no ruling lines) → `lattice` finds **0**, `stream` over-detects **4**
  low-accuracy (58–78%) "tables" that are really running text.

**Takeaway:** `lattice` is the right repair flavor (default in config) — it fires
only on genuinely ruled tables and won't manufacture tables from prose, matching
Docling's judgment. `stream` is noisy and reserved as a fallback.

## Install pain (recorded)
- **docling / docling-core version coupling.** `docling==2.15.1` declares
  `docling-core>=2.13.1,<3.0.0`; the loose bound lets pip resolve docling-core
  **2.86**, which removes symbols 2.15.1 imports → `ImportError` at import time.
  **Fix:** pin `docling-core[chunking]==2.15.1` alongside docling.
- **Do not `pip install -U docling`.** Latest (2.111) is a thin wrapper over
  `docling-slim` — a cloud/service build with **no local `document_converter` /
  PDF pipeline** (only `document_extractor` + `service_client`). Uninstalling
  slim also deletes shared files in the `docling/` namespace; recover with a
  clean reinstall of the pinned pair.
- **typer conflict.** docling 2.15.1 constrains `typer<0.13`; a `typer==0.15.1`
  pin makes the resolver fail. We ride docling's resolved **0.12.5** (`typer<0.13`).
- **torch pulls ~2 GB of CUDA wheels** (`nvidia-*-cu13`, cudnn 366 MB, …) even
  though parsing runs CPU-only (`torch.cuda.is_available() == False`).
  *Optimization for the deploy image:* install the CPU wheel explicitly
  (`pip install torch --index-url https://download.pytorch.org/whl/cpu`) to drop
  the unused GPU libs.
- **Docling downloads models on first run** (~507 MB to `~/.cache/huggingface`:
  layout + TableFormer). CI must ship these in the image / cache — **no network
  in CI** (per CLAUDE.md), so pre-bake the HF cache.

## Reproducibility
Exact resolved versions are frozen in `requirements.lock.txt` (140 packages).
Key pins: docling 2.15.1, docling-core 2.15.1, pymupdf 1.24.14,
camelot-py 0.11.0, pydantic 2.13.4, typer 0.12.5, structlog 26.1.0.
