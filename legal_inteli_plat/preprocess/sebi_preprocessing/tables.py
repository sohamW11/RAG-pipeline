"""Table quality gate + Camelot repair (CLAUDE.md §5).

Two responsibilities:

1. **Gate** — score a table grid and decide if it is *broken* (too few rows/cols,
   too many empty cells, ragged rows, or one cell hoarding the text = collapsed
   columns). Thresholds live in ``config.yaml::table_gate`` — no magic numbers.
2. **Repair** — when a *native* table is broken, re-extract it with Camelot
   (the vector-line specialist) on that page, pick the candidate that actually
   overlaps the broken table (bbox IoU), and keep it only if it is genuinely
   better. Never drop a table silently: the worse version survives with a note.

Camelot reads vector ruling lines, so it is the native repair path only; scanned
tables are the VLM hook's job (Checkpoint 4). Requires Ghostscript on the host.
"""

from __future__ import annotations

from pathlib import Path

import camelot
import structlog
from pydantic import BaseModel

from .config import PreprocessSettings, get_settings

log = structlog.get_logger(__name__)

BBox = tuple[float, float, float, float]  # (x0, y0, x1, y1), top-left origin, points


class GateMetrics(BaseModel):
    rows: int
    cols: int  # widest row
    empty_ratio: float
    ragged: bool
    max_single_cell_text_share: float


class GateResult(BaseModel):
    is_broken: bool
    reasons: list[str]  # human-readable; empty when the table is good
    metrics: GateMetrics


class RepairOutcome(BaseModel):
    grid: list[list[str]]  # the kept grid (best available; never empty-dropped)
    repaired: bool  # True iff Camelot's version replaced the original
    source_parser: str  # "docling" (kept) or "camelot" (repaired)
    note: str | None  # provenance note for the element
    original_gate: GateResult
    final_gate: GateResult


# --- gate ---------------------------------------------------------------------


def evaluate_table(
    grid: list[list[str]] | None,
    settings: PreprocessSettings | None = None,
) -> GateResult:
    """Score a grid against the §5 heuristics. Broken if ANY heuristic fires."""
    settings = settings or get_settings()
    gate = settings.table_gate
    grid = grid or []

    rows = len(grid)
    cols = max((len(r) for r in grid), default=0)
    ragged = len({len(r) for r in grid}) > 1
    cells = [c for row in grid for c in row]
    total_cells = len(cells)
    empty = sum(1 for c in cells if not c.strip())
    empty_ratio = empty / total_cells if total_cells else 1.0
    lens = [len(c.strip()) for c in cells]
    total_text = sum(lens)
    top_share = (max(lens) / total_text) if total_text else 1.0

    reasons: list[str] = []
    if rows < gate.min_rows:
        reasons.append(f"only {rows} row(s) (< {gate.min_rows})")
    if cols < gate.min_cols:
        reasons.append(f"only {cols} column(s) (< {gate.min_cols})")
    if empty_ratio > gate.max_empty_cell_ratio:
        reasons.append(f"{empty_ratio:.0%} empty cells (> {gate.max_empty_cell_ratio:.0%})")
    if ragged:
        reasons.append(f"ragged rows (column counts {sorted({len(r) for r in grid})})")
    if top_share > gate.max_single_cell_text_share:
        reasons.append(
            f"one cell holds {top_share:.0%} of text "
            f"(> {gate.max_single_cell_text_share:.0%}, collapsed columns)"
        )

    return GateResult(
        is_broken=bool(reasons),
        reasons=reasons,
        metrics=GateMetrics(
            rows=rows,
            cols=cols,
            empty_ratio=empty_ratio,
            ragged=ragged,
            max_single_cell_text_share=top_share,
        ),
    )


# --- repair -------------------------------------------------------------------


def _quality_key(gate: GateResult, grid: list[list[str]]) -> tuple:
    """Sortable "goodness" of a table — higher is better. Prefers a passing gate,
    then more real content, less emptiness, more structure."""
    non_empty = sum(1 for row in grid for c in row if c.strip())
    return (
        not gate.is_broken,
        non_empty,
        -gate.metrics.empty_ratio,
        gate.metrics.rows * gate.metrics.cols,
    )


def _camelot_bbox_to_topleft(camelot_bbox, page_height: float) -> BBox:
    """Camelot ``_bbox`` is ``(x1, y1_bottom, x2, y2_top)`` in bottom-left points."""
    x1, y1, x2, y2 = camelot_bbox
    x0, x1_ = sorted((x1, x2))
    y0, y1_ = sorted((page_height - y2, page_height - y1))
    return (x0, y0, x1_, y1_)


def _iou(a: BBox, b: BBox) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _camelot_grid(table) -> list[list[str]]:
    return [[str(c).strip() for c in row] for row in table.df.values.tolist()]


def repair_native_table(
    pdf_path: str | Path,
    page: int,
    original_grid: list[list[str]] | None,
    *,
    settings: PreprocessSettings | None = None,
    target_bbox: BBox | None = None,
    page_height: float | None = None,
    flavor: str | None = None,
) -> RepairOutcome:
    """Repair one broken native table on ``page`` (1-indexed) with Camelot.

    If ``target_bbox`` (top-left points) + ``page_height`` are given, the Camelot
    candidate that overlaps it most is chosen — so on a multi-table page the
    right table is repaired. The original is replaced only if Camelot's version
    is strictly better by the gate; otherwise it survives with a note.
    """
    settings = settings or get_settings()
    original_grid = original_grid or []
    original_gate = evaluate_table(original_grid, settings)

    def keep(note: str | None) -> RepairOutcome:
        return RepairOutcome(
            grid=original_grid,
            repaired=False,
            source_parser="docling",
            note=note,
            original_gate=original_gate,
            final_gate=original_gate,
        )

    if not original_gate.is_broken:
        return keep(None)  # nothing to repair
    if not settings.parsers.camelot.enabled:
        return keep("table broken; camelot disabled")

    flavor = flavor or settings.parsers.camelot.flavor
    try:
        tables = camelot.read_pdf(str(pdf_path), pages=str(page), flavor=flavor)
    except Exception as exc:  # noqa: BLE001 - one bad page must not kill the batch
        log.warning("tables.camelot_error", page=page, error=str(exc))
        return keep(f"table broken; camelot error: {type(exc).__name__}")

    if tables.n == 0:
        return keep(f"table broken; camelot ({flavor}) found no table on page {page}")

    # Choose the candidate that best overlaps the target, else the best-quality one.
    candidates = list(tables)
    chosen = None
    if target_bbox is not None and page_height is not None:
        scored = [
            (_iou(target_bbox, _camelot_bbox_to_topleft(t._bbox, page_height)), t)
            for t in candidates
        ]
        best_iou, best_t = max(scored, key=lambda s: s[0])
        if best_iou > 0.0:
            chosen = best_t
    if chosen is None:
        chosen = max(
            candidates,
            key=lambda t: _quality_key(evaluate_table(_camelot_grid(t), settings), _camelot_grid(t)),
        )

    cand_grid = _camelot_grid(chosen)
    cand_gate = evaluate_table(cand_grid, settings)

    if _quality_key(cand_gate, cand_grid) > _quality_key(original_gate, original_grid):
        note = f"table repaired via camelot ({flavor}): {'; '.join(original_gate.reasons)} -> "
        note += "ok" if not cand_gate.is_broken else f"still broken ({'; '.join(cand_gate.reasons)})"
        log.info("tables.repaired", page=page, flavor=flavor, still_broken=cand_gate.is_broken)
        return RepairOutcome(
            grid=cand_grid,
            repaired=True,
            source_parser="camelot",
            note=note,
            original_gate=original_gate,
            final_gate=cand_gate,
        )

    return keep(f"table broken; camelot ({flavor}) no better, kept docling")
