"""Table gate + Camelot repair tests (CLAUDE.md §5).

The gate is exercised with synthetic grids (fast, no PDF). Repair is exercised
against the real ruled table on page 2 of the bankers-to-an-issue fixture.
"""

from __future__ import annotations

import pytest

from sebi_preprocessing.config import PreprocessSettings
from sebi_preprocessing.parse_native import parse_native
from sebi_preprocessing.tables import evaluate_table, repair_native_table


@pytest.fixture(scope="session")
def settings() -> PreprocessSettings:
    return PreprocessSettings()  # min_rows/cols=2, max_empty=0.40, max_share=0.60


# --- gate: good tables --------------------------------------------------------


def test_good_table_passes(settings) -> None:
    # A realistic multi-row abbreviations table: short keys + longer values, but
    # enough rows that no single cell dominates the total text (real table = 14%).
    grid = [
        ["Abbr", "Meaning"],
        ["ASBA", "Application Supported by Blocked Amount"],
        ["BTI", "Bankers to an Issue"],
        ["CFD", "Corporation Finance Department"],
        ["DP", "Depository Participant"],
        ["KYC", "Know Your Client"],
    ]
    result = evaluate_table(grid, settings)
    assert not result.is_broken
    assert result.reasons == []
    assert result.metrics.rows == 6 and result.metrics.cols == 2


# --- gate: each broken heuristic in isolation ---------------------------------


def test_single_column_is_broken(settings) -> None:
    result = evaluate_table([["a"], ["b"], ["c"]], settings)
    assert result.is_broken
    assert any("column" in r for r in result.reasons)


def test_single_row_is_broken(settings) -> None:
    result = evaluate_table([["a", "b", "c"]], settings)
    assert result.is_broken
    assert any("row" in r for r in result.reasons)


def test_mostly_empty_is_broken(settings) -> None:
    grid = [["a", "", ""], ["", "", ""], ["", "", ""]]  # 8/9 empty
    result = evaluate_table(grid, settings)
    assert result.is_broken
    assert any("empty" in r for r in result.reasons)


def test_ragged_rows_are_broken(settings) -> None:
    grid = [["a", "b"], ["c", "d", "e"], ["f", "g"]]
    result = evaluate_table(grid, settings)
    assert result.is_broken
    assert any("ragged" in r for r in result.reasons)


def test_collapsed_columns_are_broken(settings) -> None:
    # one cell hoards ~all the text => collapsed columns
    grid = [["x", "y"], ["a" * 200, "z"]]
    result = evaluate_table(grid, settings)
    assert result.is_broken
    assert any("collapsed" in r for r in result.reasons)


def test_empty_grid_is_broken(settings) -> None:
    assert evaluate_table([], settings).is_broken
    assert evaluate_table(None, settings).is_broken


# --- repair: Camelot on the real ruled table ---------------------------------


@pytest.fixture(scope="session")
def bankers_pdf(native_table_pdf):
    return native_table_pdf


def test_broken_table_repaired_by_camelot(bankers_pdf, settings) -> None:
    # Simulate Docling collapsing the page-2 abbreviations table into one column.
    broken = [["ASBA Application Supported by Blocked Amount"], ["BTI Bankers to Issue"]]
    assert evaluate_table(broken, settings).is_broken

    # page-2 table bbox in top-left points (from Camelot _bbox, page height 792)
    outcome = repair_native_table(
        bankers_pdf,
        page=2,
        original_grid=broken,
        settings=settings,
        target_bbox=(89.0, 72.0, 566.0, 376.0),
        page_height=792.0,
    )
    assert outcome.repaired is True
    assert outcome.source_parser == "camelot"
    assert all(len(row) == 2 for row in outcome.grid)  # recovered 2 columns
    assert any("ASBA" in c for row in outcome.grid for c in row)
    assert not outcome.final_gate.is_broken


def test_good_table_not_touched(bankers_pdf, settings) -> None:
    # A real Docling table from the fixture already passes the gate → no repair.
    parsed = parse_native(bankers_pdf)
    good = next(el.table for el in parsed.elements if el.label == "table")
    assert not evaluate_table(good, settings).is_broken
    outcome = repair_native_table(bankers_pdf, page=2, original_grid=good, settings=settings)
    assert outcome.repaired is False
    assert outcome.source_parser == "docling"
    assert outcome.grid == good


def test_no_ruled_table_keeps_original(bankers_pdf, settings) -> None:
    # Page 1 of the bankers circular has no ruled table; a broken input stays put
    # (Camelot lattice fires only on genuine ruling lines — no hallucination).
    broken = [["a mashed single cell of running text with no structure at all"]]
    outcome = repair_native_table(
        bankers_pdf, page=1, original_grid=broken, settings=settings, flavor="lattice"
    )
    assert outcome.repaired is False
    assert outcome.grid == broken
    assert outcome.note and "camelot" in outcome.note
