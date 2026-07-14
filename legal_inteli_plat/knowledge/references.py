"""Citation extraction for SEBI legal documents (slice 2 of the knowledge base).

SEBI circulars cite each other by their circular ID and date, e.g.
    "SEBI Circular No. CIR/MRD/DP/21/2010 dated July 15, 2010 ... shall stand modified"
This module pulls those citations out of element text, classifies the relation
by the surrounding verb (supersedes / amends / read_with / references), and
harvests each document's OWN circular ID from its opening text so citations can
later be resolved to a doc_id. No third-party dependencies.
"""

from __future__ import annotations

import re

# A SEBI circular / notification identifier: 2+ uppercase-ish segments split by
# / or -, ending in a year (optionally /serial). Matches CIR/MRD/DP/21/2010,
# SEBI/HO/MIRSD/.../2017/123, SMDRP/Policy/Cir-49/2000, MRD/DoP/SE/Cir-31/2008.
CIRC_ID = re.compile(r"\b([A-Z]{2,}(?:[/-][A-Za-z0-9.]+){1,}/(?:19|20)\d\d(?:/\d+)?)\b")
DATE = re.compile(r"dated\s+([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4})", re.I)

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

# order matters: first match wins. supersedes (kills the target) is checked before
# amends (target stays live but modified).
_RELATION_RULES = [
    ("supersedes",   re.compile(r"supersess|supersede|in\s+supersession|in\s+place\s+of|"
                                r"rescind|repeal|withdrawn|stands?\s+withdrawn|replaced\s+by|"
                                r"in\s+lieu\s+of", re.I)),
    ("consolidates", re.compile(r"consolidat|subsumed|brought\s+together", re.I)),
    ("amends",       re.compile(r"amend|shall\s+stand\s+(?:modif|substitut)|modif|substitut|revis", re.I)),
    ("read_with",    re.compile(r"read\s+with|in\s+conjunction", re.I)),
]


def norm_id(s: str) -> str:
    """Canonical form for matching: uppercase, no whitespace."""
    return re.sub(r"\s+", "", s or "").upper()


def parse_date(s: str) -> str | None:
    """'July 15, 2010' -> '2010-07-15' (or None)."""
    m = re.match(r"([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", (s or "").strip())
    if not m:
        return None
    mon = _MONTHS.get(m.group(1)[:3].lower())
    if not mon:
        return None
    return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(2)):02d}"


def _classify(context: str) -> str:
    for rel, rx in _RELATION_RULES:
        if rx.search(context):
            return rel
    return "references"


def harvest_own_id(elements: list[dict], first_n: int = 8) -> str | None:
    """Each SEBI circular usually prints its own ID near the top of page 1."""
    text = " ".join((e.get("text") or "") for e in elements[:first_n])
    m = CIRC_ID.search(text)
    return norm_id(m.group(1)) if m else None


def extract(text: str) -> list[dict]:
    """Return citations found in one block of text: cited id/date + relation."""
    out: list[dict] = []
    for m in CIRC_ID.finditer(text or ""):
        ctx = text[max(0, m.start() - 90): m.start()]
        # nearest date within ~60 chars after the id
        tail = text[m.end(): m.end() + 60]
        dm = DATE.search(tail) or DATE.search(ctx)
        out.append({
            "raw": m.group(1),
            "cited_no": norm_id(m.group(1)),
            "cited_date": parse_date(dm.group(1)) if dm else None,
            "relation": _classify(ctx),
        })
    return out


def dedup(refs: list[dict]) -> list[dict]:
    seen: set = set()
    keep = []
    for r in refs:
        key = (r["cited_no"], r["cited_date"])
        if key in seen:
            continue
        seen.add(key)
        keep.append(r)
    return keep
