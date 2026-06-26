"""2.5 — fresh-ingest -> existing-wiki feedback.

THE GAP (plain terms, verified): both ``run.py`` and the cloud refresh call
``run_genesis`` with ``since="inception"`` — they re-derive the pillars from
scratch every time; NEITHER feeds a doc that arrives AFTER genesis into the
EXISTING agent wikis. Only session pulses reach wikis (via ``loop/fold.py``, on
pulses — not raw ingest Events). So a new research doc has no incremental path
into an existing agent's brain. This module is that path.

WHAT IT IS: a thin wrapper over the train-on-docs pipeline (2.4). It takes the
ingest Events that arrived after the initial genesis, groups them by source doc
(one ``source_id`` == one doc — ``agent_wiki_builder._group_anchors_by_source``
spirit), and runs each through ``train_on_doc`` so the relevant, novel ones land
as ``proposed`` DRAFT updates on the owning agent's wiki. Off-topic docs produce
no write; unroutable docs are HELD.

This is the first brick of the (deferred) autonomous-feedback layer — kept
small and proposals-only on purpose. It INHERITS 2.4's machinery; it adds no new
engine, only the "since-genesis, per-doc, into existing wikis" grouping.

Stdlib only; no network. Proposals-only — every write is ``build_agent_wiki(...,
proposal_status="proposed")`` via ``train_on_doc``. No company / person names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from genesis_contracts import Corpus, EgressGate, Event, LLM  # noqa: E402

from ingest.train_on_docs.relevance import AgentDomain
from ingest.train_on_docs.pipeline import TrainOnDocsResult, train_on_doc


@dataclass
class FeedbackResult:
    """What the post-genesis feedback pass did across the new docs.

      * ``per_doc``        — one ``TrainOnDocsResult`` per source doc processed;
      * ``proposed_for``   — owner slugs that got >=1 ``proposed`` DRAFT update;
      * ``off_topic_docs`` — source_ids dropped as off-topic (no write);
      * ``held_docs``      — source_ids HELD (unroutable, no write).
    """

    per_doc: list[TrainOnDocsResult] = field(default_factory=list)
    proposed_for: list[str] = field(default_factory=list)
    off_topic_docs: list[str] = field(default_factory=list)
    held_docs: list[str] = field(default_factory=list)

    @property
    def wrote_anything(self) -> bool:
        return bool(self.proposed_for)


def _group_events_by_source(events: Iterable[Event]) -> dict[str, list[Event]]:
    """One source DOC == one ``source_id``; its events = every event citing it.
    First-seen ordering of source_ids (mirrors
    ``agent_wiki_builder._group_anchors_by_source``)."""
    grouped: dict[str, list[Event]] = {}
    for e in events:
        grouped.setdefault(e.source_id, []).append(e)
    return grouped


def feed_event_to_wiki(
    new_events: Sequence[Event],
    corpus: Corpus,
    llm: LLM,
    egress: EgressGate,
    *,
    agent_domains: Sequence[AgentDomain] | None = None,
    wiki_pages_for: "callable | None" = None,
    today: str = "",
    allowlist=None,
) -> FeedbackResult:
    """Route post-genesis ingest Events into existing agent wikis (proposals-only).

    Each distinct source doc among ``new_events`` is run through ``train_on_doc``;
    a relevant, routable, novel doc lands a ``proposed`` DRAFT update on its
    owner's wiki. Off-topic -> no write; unroutable -> HELD.

    Args:
        new_events: the ingest Events that arrived AFTER the initial genesis.
        corpus: the corpus grounding the pages (should contain ``new_events``).
        llm / egress: injected through to ``build_agent_wiki`` (stubs in tests).
        agent_domains: the agent table; defaults to the pipeline's default.
        wiki_pages_for: ``slug -> list[str]`` of the owner's EXISTING wiki pages
            (so an already-known fact is deduped out). Defaults to "none".
        today: ISO date stamped on the built page's log.
        allowlist: optional company-floor allowlist.

    Returns a ``FeedbackResult``.
    """
    result = FeedbackResult()
    for source_id, doc_events in _group_events_by_source(new_events).items():
        res = train_on_doc(
            doc_events,
            corpus,
            llm,
            egress,
            agent_domains=agent_domains,
            wiki_pages_for=wiki_pages_for,
            today=today,
            allowlist=allowlist,
        )
        result.per_doc.append(res)
        if res.status == "off_topic":
            result.off_topic_docs.append(source_id)
        elif res.status == "held":
            result.held_docs.append(source_id)
        elif res.status == "proposed":
            for owner in res.proposed_pages:
                if owner not in result.proposed_for:
                    result.proposed_for.append(owner)
    return result


__all__ = ["FeedbackResult", "feed_event_to_wiki"]
