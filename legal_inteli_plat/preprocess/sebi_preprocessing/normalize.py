"""Normalize raw parser outputs into DocumentElement[] and attach provenance.

Every element gets doc_id, part, source_file, page, bbox, source_parser. If a
parser cannot supply a bbox, the reason is recorded — no untagged elements.

Implemented in Checkpoint 3.
"""

from __future__ import annotations
