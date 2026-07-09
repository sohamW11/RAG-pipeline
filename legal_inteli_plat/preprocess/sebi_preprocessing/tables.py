"""Table quality gate + repair (CLAUDE.md §5).

Cheap heuristics flag broken tables; broken native tables are re-extracted with
Camelot (vector lines), broken scanned tables via the VLM hook. After repair the
same check re-runs; the better version is kept and annotated — tables are never
dropped silently.

Implemented in Checkpoint 2.
"""

from __future__ import annotations
