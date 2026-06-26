"""Tests for the orchestrator + the two named adapters (2.4).

Pins the spec's bar (``test_pipeline.py``):
  * a relevant sample doc flows END-TO-END to a ``proposed`` cited DRAFT page in
    the RIGHT agent's wiki — deduped, carrying 2.2's derived tier, nothing
    applied;
  * an off-topic doc produces NO wiki write;
  * an unroutable doc is HELD (status="held"), no wiki write;
  * an all-duplicate doc produces no write (already in the wiki).

Plus the two adapters, each its own test:
  * ``doc_anchors(events) -> list[Anchor]`` (de-duped, .anchor()-derived);
  * ``verdict_to_routable(verdict, doc_text) -> RoutableDoc`` (candidates ->
    candidate_owners + relevance_score).

Deterministic; stub LLM; no network. The builder's WIKI_ROOT is pointed at a
temp dir (no pollution of genesis/out/).
"""

from __future__ import annotations

import json
import os

import pytest

from genesis_contracts import Anchor, EgressGate, Event, InMemoryCorpus

import agent_wiki_builder as awb
from ingest.train_on_docs.relevance import AgentDomain, gate_document
from ingest.train_on_docs.pipeline import (
    doc_anchors,
    train_on_doc,
    verdict_to_routable,
)


# --------------------------------------------------------------------------- #
# Stubs (mirror the genesis test conventions)                                  #
# --------------------------------------------------------------------------- #


class StubLLM:
    """Canned JSON keyed on the system role (source distill vs concept synth)."""

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        if "synthesize" in system:
            return json.dumps({"summary": "Product domain synthesis.", "themes": ["roadmap"]})
        return json.dumps({"what_it_is": "A product roadmap note.",
                           "known": ["roadmap = Q3 feature push"]})


# A small explicit agent table (generic; no company content).
PRODUCT = AgentDomain("specialist-product", "product",
                      ("product", "feature", "roadmap", "design", "prototype"))
GTM = AgentDomain("growth-marketing", "gtm",
                  ("pricing", "sales", "market", "campaign"))
TABLE = [PRODUCT, GTM]


@pytest.fixture()
def wiki_root(tmp_path, monkeypatch):
    root = str(tmp_path / "wiki")
    monkeypatch.setattr(awb, "WIKI_ROOT", root)
    return root


def _doc_events(*texts, source_id="src-roadmap", kind="email"):
    return [
        Event(
            event_id=f"e{i}",
            observed_at=f"2026-06-2{i}T00:00:00Z",
            kind=kind,
            text=t,
            source_id=source_id,
            locator=f"m{i}",
        )
        for i, t in enumerate(texts, start=1)
    ]


# --------------------------------------------------------------------------- #
# Adapter 1 — doc_anchors                                                       #
# --------------------------------------------------------------------------- #


def test_doc_anchors_derives_deduped_anchors_from_events():
    evs = _doc_events(
        "the product roadmap",
        "the product roadmap",   # same source_id+locator pair? different locator below
    )
    # force a duplicate (source_id, kind, locator) plus a distinct one
    evs = [
        Event("e1", "2026-06-21T00:00:00Z", "email", "a", "src-x", "m1"),
        Event("e2", "2026-06-22T00:00:00Z", "email", "b", "src-x", "m1"),  # dup triple
        Event("e3", "2026-06-23T00:00:00Z", "email", "c", "src-x", "m2"),  # distinct
    ]
    anchors = doc_anchors(evs)
    assert anchors == [Anchor("src-x", "email", "m1"), Anchor("src-x", "email", "m2")]


def test_doc_anchors_skips_events_with_no_source_id():
    evs = [Event("e1", "2026-06-21T00:00:00Z", "email", "a", "", "m1")]
    assert doc_anchors(evs) == []


# --------------------------------------------------------------------------- #
# Adapter 2 — verdict_to_routable                                              #
# --------------------------------------------------------------------------- #


def test_verdict_to_routable_maps_candidates_to_owners_and_score():
    evs = _doc_events("the product feature roadmap design", source_id="d1")
    v = gate_document(evs, TABLE)
    rd = verdict_to_routable(v, "the product feature roadmap design")
    assert rd.doc_id == "d1"
    assert rd.candidate_owners and rd.candidate_owners[0] == "specialist-product"
    assert rd.relevance_score == v.candidates[0].score


def test_verdict_to_routable_no_owner_yields_empty_candidate_owners():
    # an on-company-but-no-owner verdict (candidates=()) -> empty owners (HOLD).
    class _Allow:
        def contains_any(self, ids):
            return True

    evs = _doc_events("lunch?", source_id="d2")
    v = gate_document(evs, TABLE, allowlist=_Allow())
    assert v.kept and v.candidates == ()
    rd = verdict_to_routable(v, "lunch?")
    assert rd.candidate_owners == ()
    assert rd.relevance_score == 0.0


# --------------------------------------------------------------------------- #
# End-to-end — the integration bar                                            #
# --------------------------------------------------------------------------- #


def test_relevant_doc_flows_to_a_proposed_cited_draft_page(wiki_root):
    evs = _doc_events(
        "the product feature roadmap and design for the next prototype launch",
        source_id="src-roadmap",
    )
    corpus = InMemoryCorpus(evs)
    res = train_on_doc(evs, corpus, StubLLM(), EgressGate(), today="2026-06-26")

    assert res.status == "proposed"
    # routed to the product specialist, and ONLY a proposed DRAFT page was made.
    assert "specialist-product" in res.proposed_pages
    built = res.proposed_pages["specialist-product"]

    # the index carries proposal_status: proposed (nothing ratified/applied).
    with open(built.index_path, "r", encoding="utf-8") as fh:
        index = fh.read()
    assert "proposal_status: proposed" in index
    assert "DRAFT" in index

    # a cited source page exists and carries the source anchor (2.2's tier).
    assert built.source_pages, "expected a cited source page"
    with open(built.source_pages[0], "r", encoding="utf-8") as fh:
        page = fh.read()
    assert "## Source anchors" in page
    assert "src-roadmap" in page
    # 2.2 derived tier: a machine-built, cited page is UNVERIFIED (never VERIFIED).
    assert "UNVERIFIED" in page
    assert "VERIFIED\n" not in page.replace("UNVERIFIED", "")


def test_off_topic_doc_produces_no_wiki_write(wiki_root):
    evs = _doc_events(
        "reminder: dentist cleaning appointment Tuesday at 3pm",
        source_id="src-dentist",
    )
    corpus = InMemoryCorpus(evs)
    res = train_on_doc(evs, corpus, StubLLM(), EgressGate())
    assert res.status == "off_topic"
    assert res.proposed_pages == {}
    # nothing was written under the (temp) wiki root.
    assert not os.path.isdir(wiki_root) or os.listdir(wiki_root) == []


def test_unroutable_doc_is_held_no_write(wiki_root):
    # On-company via allowlist, but zero domain overlap -> held, no write.
    class _Allow:
        def contains_any(self, ids):
            return True

    evs = [
        Event("e1", "2026-06-21T00:00:00Z", "email", "see you at lunch tomorrow",
              "src-lunch", "m1", participants=("alice@example.com",)),
    ]
    corpus = InMemoryCorpus(evs)
    res = train_on_doc(evs, corpus, StubLLM(), EgressGate(), allowlist=_Allow())
    assert res.status == "held"
    assert res.proposed_pages == {}


def test_all_duplicate_doc_produces_no_write(wiki_root):
    # The owner's wiki already cites this exact anchor -> dedup leaves nothing.
    evs = _doc_events(
        "the product feature roadmap design prototype",
        source_id="src-known",
    )
    corpus = InMemoryCorpus(evs)
    anchor = Anchor("src-known", "email", "m1")
    existing_page = (
        "# Source 01 — src-known\n\n"
        "## What's known\n\n- the product feature roadmap design prototype\n\n"
        "## Source anchors (citations)\n\n"
        f"- {awb._anchor_md(anchor)}\n"
    )

    def pages_for(slug):
        return [existing_page] if slug == "specialist-product" else []

    res = train_on_doc(
        evs, corpus, StubLLM(), EgressGate(),
        wiki_pages_for=pages_for, today="2026-06-26",
    )
    assert res.status == "all_duplicate"
    assert res.proposed_pages == {}
