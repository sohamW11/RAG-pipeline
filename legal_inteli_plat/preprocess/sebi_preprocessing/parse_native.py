"""Docling wrapper — the main worker for native pages (CLAUDE.md §2).

Docling does the entire native page in one pass: text, layout, reading order,
AND tables. There is deliberately no "route native text to PyMuPDF" branch.
Produces raw elements (with page + bbox) that normalize.py maps to the schema.

Implemented in Checkpoint 1.
"""

from __future__ import annotations
