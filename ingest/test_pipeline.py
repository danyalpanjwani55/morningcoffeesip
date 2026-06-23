"""Tests for ingest.pipeline + ingest.corpus, and the load-bearing integration:
``run_genesis`` consumes an ``IngestedCorpus`` produced from REAL adapters.

This is the whole point of LANE A — genesis must run on ingested real data, not
just the synthetic InMemoryCorpus. The final test proves the seam end to end.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from genesis_contracts import EgressGate, Event
from genesis_pipeline import run_genesis
from ingest import IngestedCorpus, ingest_records, ingest_sources
from ingest.adapters import EmailAdapter, LocalFilesAdapter


# --------------------------------------------------------------------------- #
# IngestedCorpus conforms to the genesis Corpus protocol                       #
# --------------------------------------------------------------------------- #


def test_corpus_events_since_inception_returns_all_sorted():
    events = [
        Event("b", "2026-06-20T00:00:00Z", "note", "x", "s2"),
        Event("a", "2026-06-18T00:00:00Z", "note", "y", "s1"),
    ]
    corpus = IngestedCorpus(events)
    got = list(corpus.events_since("inception"))
    assert [e.observed_at for e in got] == ["2026-06-18T00:00:00Z", "2026-06-20T00:00:00Z"]
    assert len(corpus) == 2


def test_corpus_events_since_date_filters_strictly_newer():
    events = [
        Event("a", "2026-06-18T00:00:00Z", "note", "y", "s1"),
        Event("b", "2026-06-20T00:00:00Z", "note", "x", "s2"),
    ]
    corpus = IngestedCorpus(events)
    got = list(corpus.events_since("2026-06-19T00:00:00Z"))
    assert [e.event_id for e in got] == ["b"]


def test_corpus_satisfies_protocol_structurally():
    from genesis_contracts import Corpus

    assert isinstance(IngestedCorpus([]), Corpus)


# --------------------------------------------------------------------------- #
# Pipeline: sanitize -> dedup -> normalize accounting                          #
# --------------------------------------------------------------------------- #


def test_pipeline_drops_private_and_keeps_clean():
    records = [
        {"kind": "note", "source_id": "n1", "text": "clean note about roadmap"},
        {"kind": "note", "source_id": "n2", "text": "api_key = sk-ABCD1234ABCD1234ABCD"},  # pragma: allowlist secret
    ]
    result = ingest_records(records)
    assert result.kept == 1
    assert result.dropped_private == 1
    [ev] = result.corpus.all_events()
    assert ev.source_id == "n1"
    # the private body never became an Event
    assert all("sk-" not in e.text for e in result.corpus.all_events())


def test_pipeline_dedups_by_stable_key():
    records = [
        {"kind": "email", "source_id": "<dup@x>", "text": "first"},
        {"kind": "email", "source_id": "<dup@x>", "text": "second (same id)"},
    ]
    result = ingest_records(records)
    assert result.kept == 1
    assert result.dropped_duplicate == 1


def test_pipeline_drops_empty_body_records():
    # A record whose only text is a subject/title (e.g. an HTML-only email) has
    # no body substance to ingest -> dropped_empty, never a contentless Event.
    records = [
        {"kind": "email", "source_id": "<html@x>", "subject": "html only", "title": "html only", "text": ""},
        {"kind": "note", "source_id": "n1", "text": "real body content"},
    ]
    result = ingest_records(records)
    assert result.kept == 1
    assert result.dropped_empty == 1
    assert all(e.text for e in result.corpus.all_events())  # no empty-text Event


def test_pipeline_event_id_is_the_dedupe_key():
    result = ingest_records([{"kind": "note", "source_id": "n1", "text": "x"}])
    [ev] = result.corpus.all_events()
    assert ev.event_id.startswith("note:id:sha256-")


def test_ingest_sources_from_adapters(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text("decision: ship in October\n", encoding="utf-8")
    (tmp_path / "mail").mkdir()
    # A sent message (From == the founder) establishes a@x.com as a correspondent,
    # so the inbound below passes the email lane's SENT-folder filter.
    (tmp_path / "mail" / "0-sent.eml").write_text(
        "From: me@co.com\r\nTo: a@x.com\r\nSubject: kickoff\r\nMessage-ID: <s@co>\r\n"
        "Content-Type: text/plain\r\n\r\nlet's talk Q4\r\n",
        encoding="utf-8",
    )
    (tmp_path / "mail" / "m.eml").write_text(
        "From: a@x.com\r\nTo: me@co.com\r\nSubject: hi\r\nMessage-ID: <m@x>\r\n"
        "Content-Type: text/plain\r\n\r\nhiring plan for Q4\r\n",
        encoding="utf-8",
    )
    result = ingest_sources(
        [
            LocalFilesAdapter(root=tmp_path / "notes"),
            EmailAdapter(path=tmp_path / "mail", user_email="me@co.com"),
        ]
    )
    assert result.kept == 2
    kinds = sorted(e.kind for e in result.corpus.all_events())
    assert kinds == ["email", "note"]


# --------------------------------------------------------------------------- #
# THE integration: genesis runs on a real-adapter IngestedCorpus               #
# --------------------------------------------------------------------------- #


class _StubLLM:
    """Deterministic LLM so the genesis run is reproducible. Proposes one MI and
    one 'gtm' agent, citing real evidence ids the corpus actually produced."""

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        if "PILLAR:" in user:
            return json.dumps(
                [{"title": "Win the first segment",
                  "rationale": "Repeated pricing decisions point to a focused GTM push.",
                  "confidence": "medium", "anchor_ids": [0]}]
            )
        if "CORPUS EVIDENCE" in user:
            return json.dumps(
                [{"slug": "gtm-lead", "domain": "go-to-market",
                  "rationale": "Pricing + customer threads recur enough to warrant a lead.",
                  "anchor_ids": [0, 1, 2]}]
            )
        return "[]"


def test_run_genesis_consumes_ingested_corpus_from_real_files(tmp_path: Path):
    notes = tmp_path / "notes"
    notes.mkdir()
    # Fact-bearing notes (the genesis pipeline parses a leading "key = value").
    (notes / "pricing-1.md").write_text("list_price = 4900\nearly pricing\n", encoding="utf-8")
    (notes / "pricing-2.md").write_text("list_price = 5200\npricing review\n", encoding="utf-8")
    (notes / "launch.md").write_text("launch_date = 2026-10-15\nship target\n", encoding="utf-8")
    (notes / "customer.md").write_text("customer onboarding for the first deal\n", encoding="utf-8")
    # A private note that MUST be dropped before genesis ever sees it.
    (notes / "secret.md").write_text("api_key = sk-ABCD1234ABCD1234ABCD\n", encoding="utf-8")  # pragma: allowlist secret

    result = ingest_sources([LocalFilesAdapter(root=notes)])
    assert result.dropped_private == 1
    assert result.kept == 4

    packet = run_genesis(
        result.corpus,
        roster=["product", "ops"],
        since="inception",
        llm=_StubLLM(),
        egress=EgressGate(),
        write_drafts=False,   # keep the test side-effect free
    )

    # Genesis produced an operator review packet from REAL ingested data.
    assert packet.summary_md.strip()
    # every proposal is proposed + anchored (the genesis rails)
    assert packet.proposals, "expected genesis to derive at least one proposal"
    for p in packet.proposals:
        assert p.status == "proposed"
        assert p.is_anchored()
    # the private content never reached a pillar
    all_claim_text = " ".join(
        c.summary for pillar in packet.pillars.values() for c in pillar.claims
    )
    assert "sk-" not in all_claim_text
    # the conflicting list_price facts were ingested into the gtm pillar
    assert "gtm" in packet.pillars
