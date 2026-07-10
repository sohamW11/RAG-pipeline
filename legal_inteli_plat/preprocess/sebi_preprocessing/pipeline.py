"""Per-document orchestration of steps 1..6 (CLAUDE.md §2).

Idempotent and resumable, keyed by doc_id (+ part). Per-page and per-document
errors are isolated to errors[] and the log; one bad PDF never aborts the batch.

Implemented in Checkpoint 3 (hardened in Checkpoint 4).
"""

from __future__ import annotations
