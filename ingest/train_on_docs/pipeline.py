"""The orchestrator — events -> relevance -> router -> dedup -> build_agent_wiki.

THE FLOW (one source doc == one ``source_id``):

    events (one source_id)
      -> [1] relevance.gate_document  (keep/drop + candidate owners)
      -> [2] router.route_doc         (finalize owner[s] or HOLD)
      -> [3] dedup.dedup_anchors_vs_wiki (drop already-cited/stated anchors)
      -> [4] agent_wiki_builder.build_agent_wiki(owner, novel_anchors, corpus)
      -> proposed DRAFT pages (the builder's derived tier)

The currency is ``(Anchor, corpus)`` — ``build_agent_wiki`` takes Anchors + a
corpus and re-derives the facts; there is no Events->Claims door. This module
owns the TWO tiny named adapters that make the seam work, each its own test:

  * ``doc_anchors(events) -> list[Anchor]`` — the doc's citations. Each ``Event``
    yields its ``.anchor()`` (source_id + kind + locator — exactly
    ``fold.py:Pulse.anchors``); de-duped, deterministic.
  * ``verdict_to_routable(verdict, doc_text) -> RoutableDoc`` — maps a kept
    ``RelevanceVerdict``'s ``candidates`` -> ``candidate_owners`` slugs and
    ``relevance_score`` = the best candidate's score.

Discipline (CODE):
  * **Off-topic -> NO write.** A dropped ``RelevanceVerdict`` returns early.
  * **Unroutable -> HELD.** ``status="unassigned"`` returns early; no wiki write.
  * **All-duplicate -> no write.** If dedup leaves zero novel anchors, no page.
  * **Proposals-only.** The wiki write is ``build_agent_wiki(..., proposal_status
    ="proposed")`` — DRAFT pages; nothing applied/sent/promoted.
  * **Co-own = one proposed page per owner** (pull-not-push into each wiki).

Reuse: ``run.py:BASE_ROSTER`` + ``OfflineGenesisLLM._CLUSTERS`` +
``genesis_pipeline._DEFAULT_PILLAR_KEYWORDS`` build the ``AgentDomain`` table
(``coordinator`` / ``operator`` excluded — no catch-all); the three engines;
``agent_wiki_builder.build_agent_wiki`` (the page write).

Stdlib only; no network. No company / person names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from genesis_contracts import Anchor, Corpus, EgressGate, Event, LLM  # noqa: E402

import agent_wiki_builder as awb  # noqa: E402
from genesis_pipeline import _DEFAULT_PILLAR_KEYWORDS  # noqa: E402

from ingest.train_on_docs.relevance import (
    AgentDomain,
    RelevanceVerdict,
    gate_document,
)
from ingest.train_on_docs.router import RoutableDoc, RouteDecision, route_doc
from ingest.train_on_docs.dedup import DedupResult, dedup_anchors_vs_wiki


# --------------------------------------------------------------------------- #
# The agent-domain table (BASE_ROSTER + genesis clusters; no catch-all)        #
# --------------------------------------------------------------------------- #

# The three base specialists -> (pillar, the extra domain words their slug
# implies). Generic / company-agnostic. The pillar's _DEFAULT_PILLAR_KEYWORDS are
# folded in so a base specialist scores on the same vocabulary the pillar router
# uses. coordinator / operator are NOT here (no catch-all owner).
_BASE_SPECIALIST_DOMAINS: dict[str, tuple[str, tuple[str, ...]]] = {
    "specialist-legal-business": (
        "operations",
        ("legal", "business", "contract", "compliance", "regulatory", "finance",
         "ip", "patent"),
    ),
    "specialist-product": (
        "product",
        ("product", "feature", "roadmap", "design", "user", "prototype"),
    ),
    "specialist-software-build": (
        "product",
        ("software", "build", "code", "engineering", "api", "bug", "release",
         "deploy"),
    ),
}


def _genesis_cluster_domains() -> list[AgentDomain]:
    """The genesis offline-model clusters as scoreable agents (reused as the
    company-specialist half of the table). Imported lazily from ``run`` so the
    package doesn't hard-depend on the CLI module at import time."""
    try:
        from run import OfflineGenesisLLM  # noqa: E402
    except Exception:
        return []
    out: list[AgentDomain] = []
    for slug, domain, keywords in OfflineGenesisLLM._CLUSTERS:
        # pillar: the cluster is back-office -> 'operations' unless its label hits
        # a more specific default pillar bank.
        pillar = _pillar_for_keywords((domain,) + tuple(keywords)) or "operations"
        bank = tuple(dict.fromkeys((domain,) + tuple(keywords)))
        out.append(AgentDomain(slug=slug, pillar=pillar, keywords=bank))
    return out


def _pillar_for_keywords(words: Iterable[str]) -> str:
    """First default pillar whose bank shares a keyword with ``words`` (sorted,
    deterministic) — reuses the genesis pillar banks; '' if none match."""
    wset = {w.strip().lower() for w in words if str(w).strip()}
    for pillar in sorted(_DEFAULT_PILLAR_KEYWORDS):
        if wset.intersection(_DEFAULT_PILLAR_KEYWORDS[pillar]):
            return pillar
    return ""


def default_agent_domains() -> list[AgentDomain]:
    """The scoreable agent table: the three base specialists (slug words + their
    pillar's default keywords) + the genesis clusters. ``coordinator`` /
    ``operator`` are excluded (no catch-all mis-file). Deterministic order."""
    domains: list[AgentDomain] = []
    for slug, (pillar, extra) in _BASE_SPECIALIST_DOMAINS.items():
        bank = tuple(dict.fromkeys(tuple(extra) + _DEFAULT_PILLAR_KEYWORDS.get(pillar, ())))
        domains.append(AgentDomain(slug=slug, pillar=pillar, keywords=bank))
    domains.extend(_genesis_cluster_domains())
    return domains


# --------------------------------------------------------------------------- #
# The two named adapters (each its own test)                                    #
# --------------------------------------------------------------------------- #


def doc_anchors(events: Iterable[Event]) -> list[Anchor]:
    """ADAPTER — a doc's events -> its citation ``Anchor``s (de-duped).

    Each ``Event`` yields ``.anchor()`` = ``Anchor(source_id, kind, locator)`` —
    exactly the ``fold.py:Pulse.anchors`` shape ``build_agent_wiki`` consumes.
    De-duped on (source_id, kind, locator), first-seen order (deterministic).

    (Reconciliation: the spec phrased this as "each event carries
    ``source_anchors``"; the genesis ``Event`` exposes the same data via its
    ``.anchor()`` method — there is no ``source_anchors`` field — so the adapter
    builds the Anchor from the event, which is the identical (source_id, kind,
    locator) triple.)
    """
    out: list[Anchor] = []
    seen: set[tuple[str, str, str]] = set()
    for e in events:
        a = e.anchor()
        key = (a.source_id, a.kind, a.locator)
        if a.source_id and key not in seen:
            seen.add(key)
            out.append(a)
    return out


def verdict_to_routable(verdict: RelevanceVerdict, doc_text: str) -> RoutableDoc:
    """ADAPTER — a kept ``RelevanceVerdict`` + doc text -> a ``RoutableDoc``.

    Maps the verdict's ranked ``candidates`` -> ``candidate_owners`` (slugs, best
    first) and ``relevance_score`` = the best candidate's bank-normalized score.
    An on-company-but-no-owner verdict (``candidates=()``) yields an empty
    ``candidate_owners`` -> the router HOLDS it.
    """
    owners = tuple(c.slug for c in verdict.candidates)
    best = verdict.candidates[0].score if verdict.candidates else 0.0
    return RoutableDoc(
        doc_id=verdict.doc_id,
        doc_text=doc_text,
        candidate_owners=owners,
        relevance_score=best,
    )


# --------------------------------------------------------------------------- #
# The orchestrator result                                                      #
# --------------------------------------------------------------------------- #


@dataclass
class TrainOnDocsResult:
    """What ``train_on_doc`` did for one source doc.

      * ``doc_id``        — the source_id;
      * ``verdict``       — the relevance verdict (always set);
      * ``decision``      — the route decision (None if dropped before routing);
      * ``dedup``         — the dedup result (None if no novel-anchor stage ran);
      * ``proposed_pages``— ``{owner_slug: WikiBuildResult}`` for each wiki the
        doc proposed a DRAFT page into (empty when off-topic / held / all-dup);
      * ``status``        — ``"off_topic"`` | ``"held"`` | ``"all_duplicate"`` |
        ``"proposed"``.
    """

    doc_id: str
    verdict: RelevanceVerdict
    decision: RouteDecision | None = None
    dedup: DedupResult | None = None
    proposed_pages: dict = field(default_factory=dict)
    status: str = ""

    @property
    def wrote_anything(self) -> bool:
        return bool(self.proposed_pages)


# --------------------------------------------------------------------------- #
# The orchestrator                                                             #
# --------------------------------------------------------------------------- #


def train_on_doc(
    events: Sequence[Event],
    corpus: Corpus,
    llm: LLM,
    egress: EgressGate,
    *,
    agent_domains: Sequence[AgentDomain] | None = None,
    wiki_pages_for: "callable | None" = None,
    today: str = "",
    min_company_score: float = 0.04,
    min_agent_score: float = 0.06,
    min_overlap: int = 1,
    multi_margin: int = 0,
    similarity_threshold: float = 0.82,
    allowlist=None,
) -> TrainOnDocsResult:
    """Run one source doc (the events sharing a ``source_id``) end to end.

    Returns a ``TrainOnDocsResult``. Early-returns (no wiki write) when the doc is
    off-topic, held (unroutable), or wholly duplicate. Otherwise builds a
    ``proposed`` DRAFT page per owner from the NOVEL anchors only.

    Args:
        events: the doc's corpus events (all one ``source_id``).
        corpus: the corpus that grounds each page's facts (``build_agent_wiki``).
        llm / egress: injected through to ``build_agent_wiki`` (all model
            judgment + the egress rail; stubs in tests).
        agent_domains: the scoreable agent table; defaults to
            ``default_agent_domains()``.
        wiki_pages_for: ``slug -> list[str]`` returning the owner's EXISTING wiki
            page texts (for dedup). Defaults to "no existing pages" (all novel) —
            so a brand-new agent's first doc is fully kept.
        today: ISO date stamped on the built page's log.
        min_*/multi_margin/similarity_threshold: the per-engine knobs.
        allowlist: optional ``ingest.allowlist.Allowlist`` for the company floor.
    """
    domains = list(agent_domains) if agent_domains is not None else default_agent_domains()
    evs = list(events)
    doc_id = evs[0].source_id if evs else ""

    # [1] relevance-gate.
    verdict = gate_document(
        evs,
        domains,
        allowlist=allowlist,
        min_company_score=min_company_score,
        min_agent_score=min_agent_score,
    )
    if not verdict.kept:
        # off-topic / empty -> NO wiki write.
        return TrainOnDocsResult(
            doc_id=doc_id, verdict=verdict, status="off_topic"
        )

    # [2] router (on the doc text), constrained to the relevance candidates.
    doc_text = " ".join(f"{e.kind} {e.text}" for e in evs)
    routable = verdict_to_routable(verdict, doc_text)
    decision = route_doc(
        routable, domains, min_overlap=min_overlap, multi_margin=multi_margin
    )
    if decision.held:
        # unroutable -> HELD; never mis-filed, no wiki write.
        return TrainOnDocsResult(
            doc_id=doc_id, verdict=verdict, decision=decision, status="held"
        )

    # [3] dedup-vs-wiki, then [4] build a proposed DRAFT page per owner.
    candidate_anchors = doc_anchors(evs)
    proposed: dict = {}
    last_dedup: DedupResult | None = None

    for owner in decision.owners:
        existing_pages = list(wiki_pages_for(owner)) if wiki_pages_for else []
        dedup = dedup_anchors_vs_wiki(
            candidate_anchors,
            corpus,
            existing_pages,
            similarity_threshold=similarity_threshold,
        )
        last_dedup = dedup
        if not dedup.novel_anchors:
            # this owner already cites/states all of it — no page for them.
            continue
        built = awb.build_agent_wiki(
            owner,
            dedup.novel_anchors,
            corpus,
            llm,
            egress,
            domain=_pillar_label(decision.pillar),
            proposal_status="proposed",
            today=today,
        )
        proposed[owner] = built

    if not proposed:
        return TrainOnDocsResult(
            doc_id=doc_id, verdict=verdict, decision=decision, dedup=last_dedup,
            status="all_duplicate",
        )

    return TrainOnDocsResult(
        doc_id=doc_id, verdict=verdict, decision=decision, dedup=last_dedup,
        proposed_pages=proposed, status="proposed",
    )


def _pillar_label(pillar: str) -> str:
    """The pillar string used as the wiki's ``domain`` header (plain passthrough;
    kept a function so the label policy lives in one place)."""
    return pillar or "general"


__all__ = [
    "AgentDomain",
    "TrainOnDocsResult",
    "default_agent_domains",
    "doc_anchors",
    "verdict_to_routable",
    "train_on_doc",
]
