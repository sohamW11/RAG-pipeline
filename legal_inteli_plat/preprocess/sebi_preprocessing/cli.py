"""Typer CLI: ``preprocess <path|dir> [--force] [--limit N]``.

Implemented in Checkpoint 3.
"""

from __future__ import annotations

import typer

app = typer.Typer(help="SEBI PDF preprocessing (Phase 2).")


@app.command()
def preprocess(
    path: str = typer.Argument(..., help="PDF file or directory of PDFs."),
    force: bool = typer.Option(False, help="Re-parse docs already in parsed/."),
    limit: int = typer.Option(0, help="Process at most N documents (0 = all)."),
) -> None:
    """Placeholder — wired up in Checkpoint 3."""
    raise typer.Exit(code=0)
