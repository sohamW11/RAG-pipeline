"""Scanned page parsing via a pluggable English OCR adapter (CLAUDE.md §2, §9).

Detect scanned -> render -> OCR -> elements. The concrete engine sits behind a
small ``OcrAdapter.read(image) -> elements`` interface so it is swappable and
the multilingual adapter can drop in later without touching the pipeline.

English only for now. Implemented in Checkpoint 4.
"""

from __future__ import annotations
