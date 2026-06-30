"""concept_router — read-wiki-first routing (brain §3.4, SDL-68/SDL-56).

The retrieval-before-re-derivation gate, in code. An agent's ``index.md`` is now
a concept ROUTER table (Lane A builds it; this reads it via
``journal_schema.parse_router_index``). Given a task, ``route_query`` matches it
to exactly ONE concept and hands back that concept's recurrent-state and its
"read THESE source docs" directive — so the agent reads the owning primaries
BEFORE deriving a fact from scratch.

The load-bearing rule (de-welded from the brain): **no concept match is a
``new-concept`` SIGNAL, not a license to wing it.** A miss returns
``concept=None`` — never a guessed-wrong concept. Guessing the wrong owner is
worse than admitting there is no owner yet.

Match heuristic (deterministic + pure): case-insensitive word-boundary token
overlap between the query and each concept's *routing text* (its slug + its
1-line state). The concept with the most distinct matched query-tokens wins;
ties break by router-row order (the index's own ordering), so the result is a
pure function of (query, index). No I/O, stdlib only.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from typing import List, Optional

# journal_schema is a same-directory sibling; make the bare import resolve even
# when the importer's cwd / sys.path differs (mirrors fold.py's self-locating
# guard, scoped to just this module's dir).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from journal_schema import RouterRow, parse_router_index  # noqa: E402

ROUTED = "routed"
NEW_CONCEPT = "new-concept"

# Tokens too generic to carry routing signal — a query and a concept sharing
# only these must NOT count as a match (kills "the/a/of" false-positives).
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for",
        "from", "how", "i", "in", "is", "it", "its", "me", "my", "of", "on",
        "or", "our", "should", "that", "the", "their", "they", "this", "to",
        "us", "was", "we", "what", "when", "where", "which", "who", "why",
        "will", "with", "you", "your",
    }
)

# A routing token: an alphanumeric run of length >= 2 (a lone letter/digit is
# noise). Lowercased; slugs split on their hyphens too (so "customer-support"
# contributes both "customer" and "support").
_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


@dataclass
class RouteResult:
    """The routing payoff handed back to the agent.

    ``status`` is the contract: ``"routed"`` means ``concept`` is a real owner
    and ``source_docs`` is its read-THESE directive; ``"new-concept"`` means
    nothing matched — ``concept`` is ``None`` and the agent must treat the task
    as genuinely new (not derive a fact against a wrong concept).
    """

    status: str
    concept: Optional[str] = None
    recurrent_state: str = ""
    source_docs: str = ""
    matched_terms: List[str] = field(default_factory=list)


def _tokens(text: str) -> List[str]:
    """Distinct routing tokens (>=2 chars, hyphen-split, stopwords dropped), in
    first-seen order so the heuristic is order-stable and explainable."""
    out: List[str] = []
    seen = set()
    for tok in _TOKEN_RE.findall((text or "").lower()):
        if tok in _STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _concept_routing_terms(row: RouterRow) -> set:
    """The bank a query is matched against for one concept: its slug tokens +
    its 1-line state tokens (the state names the current truth, so a query that
    quotes a state phrase still routes)."""
    return set(_tokens(row.concept)) | set(_tokens(row.state))


def route_query(query: str, agent_index_md: str) -> RouteResult:
    """Route ``query`` to ONE concept in the agent's router ``index.md``.

    Parses the index via ``journal_schema.parse_router_index``, then picks the
    concept whose routing terms (slug + state) share the most distinct tokens
    with the query. No overlap anywhere -> ``new-concept`` (``concept=None``).
    Ties break by router-row order (deterministic).
    """
    rows = parse_router_index(agent_index_md)
    q_tokens = _tokens(query)
    if not rows or not q_tokens:
        return RouteResult(status=NEW_CONCEPT)

    q_set = set(q_tokens)
    best_row: Optional[RouterRow] = None
    best_hits = 0
    best_matched: List[str] = []
    for row in rows:  # router-row order => deterministic tie-break (first wins)
        terms = _concept_routing_terms(row)
        # Preserve query order in the explanation of WHY it matched.
        matched = [t for t in q_tokens if t in terms]
        hits = len(matched)
        if hits > best_hits:
            best_hits = hits
            best_row = row
            best_matched = matched

    if best_row is None or best_hits == 0:
        return RouteResult(status=NEW_CONCEPT)

    return RouteResult(
        status=ROUTED,
        concept=best_row.concept,
        recurrent_state=best_row.state,
        source_docs=best_row.source_docs,
        matched_terms=best_matched,
    )


__all__ = ["RouteResult", "route_query", "ROUTED", "NEW_CONCEPT"]
