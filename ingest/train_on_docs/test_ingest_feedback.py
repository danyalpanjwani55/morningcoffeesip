"""Tests for 2.5 — fresh-ingest -> existing-wiki feedback.

Pins the spec's bar (``test_ingest_feedback.py``):
  * an ingest Event arriving AFTER initial genesis produces a ``proposed`` wiki
    update for the owning agent.

Also covers the rails it inherits from 2.4:
  * an off-topic post-genesis event produces NO write (recorded as off_topic);
  * an unroutable post-genesis event is HELD (no write).

Deterministic; stub LLM; no network. WIKI_ROOT -> temp dir.
"""

from __future__ import annotations

import json

import pytest

from genesis_contracts import EgressGate, Event, InMemoryCorpus

import agent_wiki_builder as awb
from ingest.train_on_docs.relevance import AgentDomain
from ingest.train_on_docs.feedback import feed_event_to_wiki


class StubLLM:
    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        if "synthesize" in system:
            return json.dumps({"summary": "synthesis", "themes": ["t"]})
        return json.dumps({"what_it_is": "a note", "known": ["fact = value"]})


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


def test_post_genesis_event_proposes_a_wiki_update_for_the_owner(wiki_root):
    # Genesis already ran (we don't re-run it); a NEW ingest Event arrives.
    new_event = Event(
        event_id="e-new",
        observed_at="2026-06-26T09:00:00Z",
        kind="email",
        text="fresh research: a new product feature roadmap design prototype direction",
        source_id="src-fresh",
        locator="m1",
    )
    corpus = InMemoryCorpus([new_event])

    res = feed_event_to_wiki(
        [new_event], corpus, StubLLM(), EgressGate(),
        agent_domains=TABLE, today="2026-06-26",
    )

    assert "specialist-product" in res.proposed_for
    assert res.wrote_anything is True

    # the per-doc result is a proposed DRAFT page (proposals-only).
    assert len(res.per_doc) == 1
    doc_res = res.per_doc[0]
    assert doc_res.status == "proposed"
    built = doc_res.proposed_pages["specialist-product"]
    with open(built.index_path, "r", encoding="utf-8") as fh:
        index = fh.read()
    assert "proposal_status: proposed" in index


def test_off_topic_post_genesis_event_writes_nothing(wiki_root):
    off = Event(
        event_id="e-off",
        observed_at="2026-06-26T09:00:00Z",
        kind="email",
        text="your dentist appointment reminder for Tuesday",
        source_id="src-dentist",
        locator="m1",
    )
    corpus = InMemoryCorpus([off])
    res = feed_event_to_wiki(
        [off], corpus, StubLLM(), EgressGate(), agent_domains=TABLE
    )
    assert res.proposed_for == []
    assert "src-dentist" in res.off_topic_docs


def test_unroutable_post_genesis_event_is_held(wiki_root):
    class _Allow:
        def contains_any(self, ids):
            return True

    held = Event(
        event_id="e-held",
        observed_at="2026-06-26T09:00:00Z",
        kind="email",
        text="quick personal hello, nothing domain-relevant",
        source_id="src-hello",
        locator="m1",
        participants=("alice@example.com",),
    )
    corpus = InMemoryCorpus([held])
    res = feed_event_to_wiki(
        [held], corpus, StubLLM(), EgressGate(),
        agent_domains=TABLE, allowlist=_Allow(),
    )
    assert res.proposed_for == []
    assert "src-hello" in res.held_docs


def test_multiple_docs_route_independently(wiki_root):
    events = [
        Event("e1", "2026-06-26T09:00:00Z", "email",
              "product feature roadmap design prototype", "src-prod", "m1"),
        Event("e2", "2026-06-26T10:00:00Z", "email",
              "pricing sales market campaign funnel push", "src-gtm", "m2"),
        Event("e3", "2026-06-26T11:00:00Z", "email",
              "dentist appointment reminder", "src-off", "m3"),
    ]
    corpus = InMemoryCorpus(events)
    res = feed_event_to_wiki(
        events, corpus, StubLLM(), EgressGate(), agent_domains=TABLE,
        today="2026-06-26",
    )
    assert "specialist-product" in res.proposed_for
    assert "growth-marketing" in res.proposed_for
    assert "src-off" in res.off_topic_docs
