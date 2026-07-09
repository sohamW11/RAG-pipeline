"""PyMuPDF triage — the scout (CLAUDE.md §2, Tool roles).

Only two jobs: decide native vs scanned per page, and render scanned pages to
images. It does NOT extract content for the native path.

Implemented in Checkpoint 1.
"""

from __future__ import annotations
