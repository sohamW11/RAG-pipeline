"""SEBI preprocessing (Phase 2).

Turns a raw SEBI PDF into a list of normalized, provenance-tagged structured
elements (headings, paragraphs, tables) with an identical, predictable output
shape regardless of whether the source page was native or scanned.

Flow (see CLAUDE.md §2 — build exactly this, do not reorder):
    PyMuPDF triages native vs scanned per page
      -> native pages go through Docling (text + reading order + tables)
      -> scanned pages go through the OCR adapter (English for now)
      -> any broken table is repaired (Camelot for native, VLM for scanned)
      -> everything is normalized into one tagged DocumentElement schema.
"""

__version__ = "0.1.0"
