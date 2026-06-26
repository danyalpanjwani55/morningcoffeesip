"""Engine 1 — the relevance-gate.

THE QUESTION (plain terms): is this doc even about the company, and if so, which
specialist(s) might own it? A dentist appointment, a newsletter, a personal
thread should produce NO wiki write at all. This gate answers that on the doc's
EVENT TEXT before any heavier work, and hands the router a ranked candidate set.

Scoring (the reconciled shared contract):
  * each agent carries a keyword BANK = its (domain label + pillar keywords +
    wiki vocab), scored against the doc text via the ONE ``keyword_overlap``
    primitive, then **normalized by bank size** (``hits / bank_size``) so a big
    bank can't dominate — the fix for ``_route_pillar``'s first-match bias;
  * **company floor** = the best agent score clears ``min_company_score`` OR a
    doc participant is in the founder's ``Allowlist`` (``contains_any``). A doc
    that's about no agent's domain AND involves nobody the founder corresponds
    with is off-topic -> dropped (``reason="off_topic"``);
  * survivors return their candidates (agents scoring >= ``min_agent_score``),
    ranked best-first.

Edge — on-company-but-no-owner: a doc that clears the company floor (e.g. an
allowlisted participant) but matches NO agent domain is KEPT with
``candidates=()`` and ``reason="no_domain_match"`` — the router then HOLDS it
(``status="unassigned"``) rather than mis-filing it.

Reuse:
  * ``_overlap.keyword_overlap`` (the membership tally),
  * ``genesis_pipeline._DEFAULT_PILLAR_KEYWORDS`` (the per-pillar keyword banks),
  * ``ingest/allowlist.py:Allowlist.contains_any`` (the company-floor fallback),
  * ``agent_wiki_builder._group_anchors_by_source`` (one source_id == one doc) —
    used upstream by the pipeline; relevance is handed a single doc's events.

Pure; stdlib only; no I/O. No company / person names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from genesis_contracts import Event  # noqa: E402  (genesis/ on sys.path via package __init__)

from ingest.train_on_docs._overlap import keyword_overlap, normalized_score


# --------------------------------------------------------------------------- #
# The agent-domain table (the model the gate + router score against)           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AgentDomain:
    """One scoreable agent: a slug, a pillar (for ``_route_pillar``), and its
    keyword BANK (domain label words + pillar keywords + any wiki vocab). The
    bank is what ``keyword_overlap`` scores a doc against.

    Built once by the pipeline from ``run.py:BASE_ROSTER`` + the genesis cluster
    table + ``_DEFAULT_PILLAR_KEYWORDS``; relevance + router both consume it so
    there is ONE table, not two.
    """

    slug: str
    pillar: str
    keywords: tuple[str, ...]

    @property
    def bank_size(self) -> int:
        # distinct, non-blank keywords — matches how ``keyword_overlap`` counts.
        return len({k.strip().lower() for k in self.keywords if str(k).strip()})


@dataclass(frozen=True)
class AgentScore:
    """One agent's score for a doc (an element of a ``RelevanceVerdict``'s
    ranked ``candidates``). ``score`` is bank-normalized; ``hits`` is the raw
    tally the router reuses; ``matched_terms`` shows why."""

    slug: str
    score: float
    hits: int
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class RelevanceVerdict:
    """The ONE handoff contract: produced by relevance, consumed by the router
    (which does NOT recompute relevance — it reuses these ``candidates``).

      * ``doc_id``    — the doc's ``source_id``;
      * ``kept``      — did it clear the company floor?
      * ``on_company``— same as ``kept`` semantically (clarity for callers);
      * ``candidates``— ranked ``AgentScore``s above ``min_agent_score`` (best
        first); ``()`` when on-company-but-no-owner;
      * ``reason``    — ``"relevant"`` | ``"off_topic"`` | ``"no_domain_match"``
        | ``"empty"``.
    """

    doc_id: str
    kept: bool
    on_company: bool
    candidates: tuple[AgentScore, ...] = ()
    reason: str = ""

    @property
    def best(self) -> AgentScore | None:
        return self.candidates[0] if self.candidates else None


# --------------------------------------------------------------------------- #
# The gate                                                                      #
# --------------------------------------------------------------------------- #


def _doc_text(events: Sequence[Event]) -> str:
    """The doc's searchable haystack: every event's kind + text, joined."""
    return " ".join(f"{e.kind} {e.text}" for e in events)


def _doc_participants(events: Sequence[Event]) -> list[str]:
    """Every participant across the doc's events (for the allowlist floor)."""
    out: list[str] = []
    seen: set[str] = set()
    for e in events:
        for p in e.participants:
            if p and p not in seen:
                seen.add(p)
                out.append(p)
    return out


def gate_document(
    events: Iterable[Event],
    agent_domains: Sequence[AgentDomain],
    *,
    allowlist=None,
    min_company_score: float = 0.04,
    min_agent_score: float = 0.06,
) -> RelevanceVerdict:
    """Score ONE doc (the events sharing a ``source_id``) against the agent table.

    Returns a ``RelevanceVerdict``. A doc is KEPT iff it clears the company floor
    (best agent score >= ``min_company_score`` OR a participant is allowlisted);
    otherwise it's ``off_topic`` and dropped. Kept docs carry their candidate
    owners (agents scoring >= ``min_agent_score``), ranked best-first. A kept doc
    that matches no domain returns ``candidates=()`` + ``reason="no_domain_match"``.

    Args:
        events: the doc's corpus events (all one ``source_id``).
        agent_domains: the scoreable agent table (slug + pillar + keyword bank).
        allowlist: optional ``ingest.allowlist.Allowlist`` — its ``contains_any``
            is the company-floor fallback when domain overlap is thin.
        min_company_score: the bank-normalized floor a doc must clear on its best
            agent to count as on-company (unless an allowlisted participant
            clears it instead).
        min_agent_score: the bank-normalized floor an agent must clear to be a
            ranked candidate.
    """
    evs = list(events)
    doc_id = evs[0].source_id if evs else ""

    # Empty == no actual body TEXT (the ``kind`` label is metadata, not content);
    # a doc with only a kind and no text can't be scored or cited.
    if not evs or not any(e.text and e.text.strip() for e in evs):
        return RelevanceVerdict(
            doc_id=doc_id, kept=False, on_company=False, candidates=(), reason="empty"
        )

    haystack = _doc_text(evs)

    scored: list[AgentScore] = []
    for ad in agent_domains:
        hits, matched = keyword_overlap(haystack, ad.keywords)
        score = normalized_score(hits, ad.bank_size)
        if score > 0.0:
            scored.append(
                AgentScore(slug=ad.slug, score=score, hits=hits, matched_terms=matched)
            )

    # Rank best-first; ties broken by raw hits then slug for determinism.
    scored.sort(key=lambda s: (-s.score, -s.hits, s.slug))
    best_score = scored[0].score if scored else 0.0

    # Company floor: domain overlap OR an allowlisted participant.
    on_company = best_score >= min_company_score
    if not on_company and allowlist is not None:
        if allowlist.contains_any(_doc_participants(evs)):
            on_company = True

    if not on_company:
        return RelevanceVerdict(
            doc_id=doc_id, kept=False, on_company=False, candidates=(), reason="off_topic"
        )

    candidates = tuple(s for s in scored if s.score >= min_agent_score)
    if not candidates:
        # On-company (e.g. an allowlisted participant) but no domain owner — the
        # router HOLDS it; never mis-file it under a catch-all.
        return RelevanceVerdict(
            doc_id=doc_id,
            kept=True,
            on_company=True,
            candidates=(),
            reason="no_domain_match",
        )

    return RelevanceVerdict(
        doc_id=doc_id,
        kept=True,
        on_company=True,
        candidates=candidates,
        reason="relevant",
    )


__all__ = ["AgentDomain", "AgentScore", "RelevanceVerdict", "gate_document"]
