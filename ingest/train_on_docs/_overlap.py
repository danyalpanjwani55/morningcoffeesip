"""The ONE overlap primitive both relevance-gate and router call.

ONE membership tally, two callers (the reconciled shared contract):
  * **relevance** normalizes the tally by bank size (kills ``_route_pillar``'s
    first-match, count-nothing bias so a big keyword bank can't dominate).
  * **router** uses the RAW hit count (more matched terms = stronger owner).

Built on the SAME ``kw in haystack`` substring-membership test that
``genesis_pipeline._route_pillar`` uses — but ``_route_pillar`` returns a
*pillar* (first match wins, counts nothing); it is the MODEL for the membership
test, not the source of a tally. This adds the tally ``_route_pillar`` lacks.

Pure; stdlib only; no I/O.
"""

from __future__ import annotations

import re
from typing import Iterable


def keyword_overlap(haystack: str, keywords: Iterable[str]) -> tuple[int, tuple[str, ...]]:
    """Count how many of ``keywords`` appear in ``haystack`` (case-insensitive
    word-boundary PREFIX membership — `_route_pillar`'s test, hardened + tallied).

    Match is anchored at a word boundary but open-ended (`\\bprice` matches
    "pricing") so stemming still works, while a short keyword can no longer match
    *inside* an unrelated word ("ip" does NOT match "t-ips"/"tips"). This closes
    the substring false-positive that let off-topic docs (a "gardening tips"
    newsletter) score against banks carrying short tokens like "ip"/"ops".

    Returns ``(hits, matched_terms)``:
      * ``hits`` — the number of DISTINCT keywords found (a keyword counts once,
        however many times it occurs);
      * ``matched_terms`` — those keywords, in first-seen (bank) order, lowercased
        and de-duplicated (so a caller can show *why* a doc matched).

    Determinism: the bank is walked in its given order; a blank keyword never
    matches; an empty haystack yields ``(0, ())``.
    """
    hay = (haystack or "").lower()
    if not hay:
        return 0, ()
    matched: list[str] = []
    seen: set[str] = set()
    for kw in keywords:
        term = str(kw).strip().lower()
        if not term or term in seen:
            continue
        if re.search(r"\b" + re.escape(term), hay):
            seen.add(term)
            matched.append(term)
    return len(matched), tuple(matched)


def normalized_score(hits: int, bank_size: int) -> float:
    """``hits / bank_size`` — the relevance-side normalization that kills the
    big-bank bias (a 30-keyword bank with 2 hits scores below a 4-keyword bank
    with 2 hits). ``0.0`` when the bank is empty (nothing to match against)."""
    if bank_size <= 0:
        return 0.0
    return hits / bank_size


__all__ = ["keyword_overlap", "normalized_score"]
