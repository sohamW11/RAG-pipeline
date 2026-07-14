"""SEBI entity / concept extraction — the interpretable half of the memory layer.

A curated gazetteer of SEBI intermediaries, products, concepts and regulation
short-names, plus regexes for `Regulation N` / `Section N`. No model, no deps —
the acronyms embeddings are worst at (AIF, InvIT, LODR, UPSI) are exactly what a
gazetteer nails. Extraction is per document; docs sharing rare entities become
affinity edges (see memory.py).
"""

from __future__ import annotations

import re

# (canonical, kind, [alias regex fragments]) — matched case-insensitively, word-bounded
_GAZETTEER = [
    ("mutual_fund", "intermediary", [r"mutual funds?"]),
    ("alternative_investment_fund", "intermediary", [r"alternative investment funds?", r"AIFs?"]),
    ("infrastructure_investment_trust", "intermediary", [r"infrastructure investment trusts?", r"InvITs?"]),
    ("real_estate_investment_trust", "intermediary", [r"real estate investment trusts?", r"REITs?"]),
    ("foreign_portfolio_investor", "intermediary", [r"foreign portfolio investors?", r"FPIs?"]),
    ("foreign_institutional_investor", "intermediary", [r"foreign institutional investors?", r"FIIs?"]),
    ("qualified_institutional_buyer", "concept", [r"qualified institutional buyers?", r"QIBs?"]),
    ("registrar_transfer_agent", "intermediary", [r"registrars? to an issue", r"transfer agents?", r"RTAs?"]),
    ("portfolio_manager", "intermediary", [r"portfolio managers?"]),
    ("merchant_banker", "intermediary", [r"merchant bankers?"]),
    ("stock_broker", "intermediary", [r"stock ?brokers?", r"sub-?brokers?"]),
    ("depository", "intermediary", [r"depositor(?:y|ies)"]),
    ("depository_participant", "intermediary", [r"depository participants?", r"\bDPs?\b"]),
    ("credit_rating_agency", "intermediary", [r"credit rating agenc(?:y|ies)", r"CRAs?"]),
    ("custodian", "intermediary", [r"custodians?"]),
    ("investment_adviser", "intermediary", [r"investment advisers?"]),
    ("research_analyst", "intermediary", [r"research analysts?"]),
    ("debenture_trustee", "intermediary", [r"debenture trustees?"]),
    ("clearing_corporation", "intermediary", [r"clearing corporations?"]),
    ("stock_exchange", "intermediary", [r"stock exchanges?"]),
    ("listed_entity", "entity", [r"listed (?:entit(?:y|ies)|compan(?:y|ies))"]),
    ("promoter", "entity", [r"promoters?"]),
    ("upsi", "concept", [r"unpublished price sensitive information", r"UPSI"]),
    ("insider_trading", "concept", [r"insider trading"]),
    ("related_party_transaction", "concept", [r"related party transactions?", r"RPTs?"]),
    ("minimum_public_shareholding", "concept", [r"minimum public shareholding", r"\bMPS\b"]),
    ("corporate_governance", "concept", [r"corporate governance"]),
    ("delisting", "concept", [r"delisting"]),
    ("buyback", "concept", [r"buy-?backs?"]),
    ("takeover", "concept", [r"takeovers?", r"open offers?"]),
    ("preferential_issue", "concept", [r"preferential issues?"]),
    ("rights_issue", "concept", [r"rights issues?"]),
    ("ipo", "concept", [r"initial public offer(?:ing)?", r"IPOs?"]),
    ("qip", "concept", [r"qualified institutions? placement", r"QIP"]),
    ("lodr", "regulation_name", [r"LODR", r"listing obligations and disclosure requirements"]),
    ("icdr", "regulation_name", [r"ICDR", r"issue of capital and disclosure requirements"]),
    ("sast", "regulation_name", [r"SAST", r"substantial acquisition of shares and takeovers"]),
    ("pit", "regulation_name", [r"prohibition of insider trading"]),
]

# compile one regex per canonical (alternation of its aliases)
_COMPILED = [(norm, kind, re.compile(r"\b(?:" + "|".join(als) + r")\b", re.I))
             for norm, kind, als in _GAZETTEER]

_REGULATION = re.compile(r"\bregulation\s+(\d+[A-Z]{0,3})\b", re.I)
_SECTION = re.compile(r"\bsection\s+(\d+[A-Z]{0,3})\b", re.I)

# display names for the canonical ids
DISPLAY = {norm: norm.replace("_", " ") for norm, _, _ in _GAZETTEER}


def extract(text: str) -> dict[str, tuple[str, int]]:
    """text -> {norm: (kind, count)} for all gazetteer + regulation/section hits."""
    out: dict[str, tuple[str, int]] = {}
    if not text:
        return out
    for norm, kind, rx in _COMPILED:
        n = len(rx.findall(text))
        if n:
            out[norm] = (kind, n)
    for m in _REGULATION.finditer(text):
        key = f"regulation_{m.group(1).lower()}"
        k, c = out.get(key, ("provision", 0))
        out[key] = ("provision", c + 1)
    for m in _SECTION.finditer(text):
        key = f"section_{m.group(1).lower()}"
        k, c = out.get(key, ("provision", 0))
        out[key] = ("provision", c + 1)
    return out


def display_name(norm: str) -> str:
    if norm in DISPLAY:
        return DISPLAY[norm]
    if norm.startswith("regulation_"):
        return "Regulation " + norm.split("_", 1)[1].upper()
    if norm.startswith("section_"):
        return "Section " + norm.split("_", 1)[1].upper()
    return norm.replace("_", " ")
