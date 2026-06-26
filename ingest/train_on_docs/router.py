"""Engine 3 — the agent-router.

THE QUESTION (plain terms): given a doc that IS about the company, which
specialist(s) own it — or is it a brand-new topic nobody owns yet (HOLD it)?
The router finalizes ownership on the doc text, constrained to the candidates
the relevance-gate already surfaced (it does NOT recompute relevance).

Rules:
  * score every engine-1 candidate agent via the shared ``keyword_overlap`` —
    **raw hits** here (more matched terms = stronger owner; the relevance gate
    did the bank-normalization);
  * ``top < min_overlap`` -> ``owners=()``, ``status="unassigned"`` — **HELD,
    never mis-filed.** (Feeds the roster-proposer's evidence for a NEW agent.);
  * agents within ``multi_margin`` hits of the top -> **co-own** (pull-not-push;
    the doc feeds both wikis);
  * ``pillar`` via ``genesis_pipeline._route_pillar`` (the doc projected as one
    Event).

``coordinator`` / ``operator`` are deliberately EXCLUDED from the domain table
(no catch-all to silently absorb an unroutable doc) — the pipeline builds the
table without them; the router additionally guards against either slipping in.

Reuse:
  * ``_overlap.keyword_overlap`` (the membership tally),
  * ``genesis_pipeline._route_pillar`` + ``_DEFAULT_PILLAR_KEYWORDS`` (pillar),
  * ``loop/fold.py:Pulse.agents`` shape — ``owners`` is a tuple of slugs that
    drops straight into the fold / ``build_agent_wiki`` path,
  * ``run.py:BASE_ROSTER`` + the genesis cluster table as the ``AgentDomain``
    table (assembled by the pipeline, consumed here).

Pure; stdlib only; no I/O. No company / person names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from genesis_contracts import Event  # noqa: E402

from genesis_pipeline import (  # noqa: E402
    _DEFAULT_PILLAR_KEYWORDS,
    _route_pillar,
)

from ingest.train_on_docs._overlap import keyword_overlap
from ingest.train_on_docs.relevance import AgentDomain

# Catch-all slugs that must NEVER own a routed doc (no silent mis-file).
_EXCLUDED_OWNERS = frozenset({"coordinator", "operator"})


@dataclass(frozen=True)
class RoutableDoc:
    """The router's input — produced by the pipeline's ``verdict_to_routable``
    adapter from a kept ``RelevanceVerdict`` + the doc text.

      * ``doc_id``          — the doc's ``source_id``;
      * ``doc_text``        — the full doc text (router scores + pillars on this);
      * ``candidate_owners``— the relevance-gate's candidate slugs (the router is
        constrained to these; ``()`` means on-company-but-no-owner -> HOLD);
      * ``relevance_score`` — the best candidate's bank-normalized score (carried
        for the review surface; the router doesn't re-score relevance).
    """

    doc_id: str
    doc_text: str
    candidate_owners: tuple[str, ...] = ()
    relevance_score: float = 0.0


@dataclass(frozen=True)
class RouteDecision:
    """Where a doc goes.

      * ``owners`` — the owning slug(s); ``()`` when HELD;
      * ``pillar`` — the routed pillar (always set, for the wiki/index header);
      * ``scores`` — ``{slug: raw_hits}`` for every scored candidate;
      * ``status`` — ``"routed"`` | ``"unassigned"`` (HELD);
      * ``why``    — a one-line plain reason.
    """

    owners: tuple[str, ...]
    pillar: str
    scores: dict[str, int] = field(default_factory=dict)
    status: str = "routed"
    why: str = ""

    @property
    def held(self) -> bool:
        return self.status == "unassigned"


def _doc_as_event(doc: RoutableDoc) -> Event:
    """Project the doc as ONE Event so ``_route_pillar`` can pillar it (it reads
    ``event.kind`` + ``event.text``)."""
    return Event(
        event_id=doc.doc_id or "doc",
        observed_at="",
        kind="doc",
        text=doc.doc_text,
        source_id=doc.doc_id,
    )


def route_doc(
    doc: RoutableDoc,
    agent_domains: Sequence[AgentDomain],
    *,
    min_overlap: int = 1,
    multi_margin: int = 0,
) -> RouteDecision:
    """Finalize ownership for one on-company doc.

    Constrained to ``doc.candidate_owners`` (the relevance gate's survivors): the
    router scores only those agents, by RAW ``keyword_overlap`` hits on the doc
    text. The top scorer wins; agents within ``multi_margin`` hits co-own. If the
    top is below ``min_overlap`` (or there are no candidates), the doc is HELD
    (``status="unassigned"``) — never mis-filed.

    Args:
        doc: the routable doc (text + candidate owners from relevance).
        agent_domains: the agent table (the keyword banks to score against).
        min_overlap: the minimum raw hits the top agent needs to OWN the doc.
        multi_margin: agents within this many hits of the top co-own (0 = exact
            tie co-owns; 1 = within one hit; ...).
    """
    pillar = _route_pillar(_doc_as_event(doc), _DEFAULT_PILLAR_KEYWORDS)

    # Constrain to the relevance candidates, minus any excluded catch-all slug.
    allowed = {
        s for s in doc.candidate_owners if s not in _EXCLUDED_OWNERS
    }
    by_slug = {ad.slug: ad for ad in agent_domains if ad.slug not in _EXCLUDED_OWNERS}

    scores: dict[str, int] = {}
    for slug in allowed:
        ad = by_slug.get(slug)
        if ad is None:
            continue
        hits, _ = keyword_overlap(doc.doc_text, ad.keywords)
        scores[slug] = hits

    if not scores:
        return RouteDecision(
            owners=(), pillar=pillar, scores={}, status="unassigned",
            why="no candidate owner from relevance — held for roster review",
        )

    top = max(scores.values())
    if top < min_overlap:
        return RouteDecision(
            owners=(), pillar=pillar, scores=scores, status="unassigned",
            why=(
                f"top domain overlap {top} < min_overlap {min_overlap} — "
                "brand-new topic, held for roster review"
            ),
        )

    # Co-own: every candidate within multi_margin hits of the top. Deterministic
    # ordering: by hits desc, then slug.
    owners = tuple(
        slug
        for slug, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        if top - scores[slug] <= multi_margin
    )

    if len(owners) == 1:
        why = f"single owner {owners[0]} (overlap {scores[owners[0]]})"
    else:
        why = (
            f"co-owned by {len(owners)} agents within margin {multi_margin}: "
            + ", ".join(owners)
        )
    return RouteDecision(
        owners=owners, pillar=pillar, scores=scores, status="routed", why=why
    )


__all__ = ["RoutableDoc", "RouteDecision", "route_doc"]
