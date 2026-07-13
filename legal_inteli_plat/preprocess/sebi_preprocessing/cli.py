"""Typer CLI: ``preprocess <path|dir> [--force] [--limit N] [--out DIR]`` (CLAUDE.md §7).

Note: no ``from __future__ import annotations`` here — Typer 0.12.x needs real
runtime types on the command signature, not stringized annotations.
"""

from typing import Optional

import typer

from .config import get_settings
from .logging_config import configure_logging
from .pipeline import preprocess_path

app = typer.Typer(help="SEBI PDF preprocessing (Phase 2).")


@app.callback()
def main() -> None:
    """SEBI PDF preprocessing (Phase 2). Keeps ``preprocess`` as a named subcommand
    (a single-command Typer app would otherwise drop the command name)."""


@app.command()
def preprocess(
    path: str = typer.Argument(..., help="PDF file or directory of PDFs."),
    force: bool = typer.Option(False, help="Re-parse docs already in the output dir."),
    limit: int = typer.Option(0, help="Process at most N new documents (0 = all)."),
    out: Optional[str] = typer.Option(
        None, help="Output directory for doc JSON + manifest (default: config paths.parsed_dir)."
    ),
) -> None:
    """Parse PDFs into normalized, provenance-tagged ``parsed/{doc_id}.json``."""
    settings = get_settings()
    configure_logging(settings)
    manifest = preprocess_path(path, settings, force=force, limit=limit, out_dir=out)

    typer.echo(
        f"processed={manifest['documents_processed']} "
        f"skipped={manifest['documents_skipped']} "
        f"failed={manifest['documents_failed']} "
        f"| native_pages={manifest['native_pages']} scanned_pages={manifest['scanned_pages']} "
        f"| tables_found={manifest['tables_found']} repaired={manifest['tables_repaired']} "
        f"| elements={manifest['elements']}"
    )


if __name__ == "__main__":
    app()
