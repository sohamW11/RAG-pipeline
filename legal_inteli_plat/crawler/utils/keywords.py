"""Lightweight keyword extraction.

Derives the "main keywords" for a document from the metadata the website
exposes (title, category, year) -- no PDF parsing required. The result gives
downstream consumers (search, RAG indexing) cheap context about what a document
is, and is stored on ``documents.keywords`` as a comma-separated string.
"""

from __future__ import annotations

import re

# Common English + legal-boilerplate words that carry little signal.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "under", "from", "into", "onto", "upon",
        "of", "to", "in", "on", "at", "by", "as", "an", "a", "or", "nor", "but",
        "is", "are", "was", "were", "be", "been", "being", "this", "that",
        "these", "those", "its", "it", "their", "his", "her", "our", "your",
        "shall", "may", "will", "would", "can", "could", "should", "must",
        "amended", "amendment", "regarding", "respect", "w.e.f", "wef", "vide",
        "read", "dated", "date", "no", "nos", "sub", "re", "etc",
    }
)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9&/-]+")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def extract_keywords(
    title: str,
    *,
    category: str | None = None,
    extra: list[str] | None = None,
    limit: int = 15,
) -> list[str]:
    """Return the salient keywords for a document.

    Args:
        title: The document title (richest signal SEBI exposes).
        category: The legal category (Acts, Circulars, ...); always kept.
        extra: Additional terms to seed (e.g. department).
        limit: Maximum number of keywords to return.

    Returns:
        De-duplicated, order-preserving keyword list (category first, then any
        four-digit years found, then the salient title terms).
    """
    keywords: list[str] = []
    seen: set[str] = set()

    def _push(term: str) -> None:
        term = term.strip().lower()
        if not term or term in seen:
            return
        seen.add(term)
        keywords.append(term)

    if category:
        _push(category)
    for term in extra or []:
        _push(term)
    # Years are strong anchors for legal documents.
    for match in _YEAR_RE.finditer(title):
        _push(match.group(0))

    for token in _TOKEN_RE.findall(title):
        low = token.lower()
        if len(low) < 3 or low in _STOPWORDS:
            continue
        _push(low)
        if len(keywords) >= limit:
            break

    return keywords[:limit]
