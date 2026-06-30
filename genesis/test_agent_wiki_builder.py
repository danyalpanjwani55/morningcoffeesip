"""Tests for the agent-wiki builder (LANE L3 — un-defer train-on-docs).

Deterministic; stub LLM; no network. Pins the contract from the agent-brain-wiki
schema + the L3 acceptance bar:

  * a proposed agent WITH sources gets a wiki with CITED pages (index + log +
    >=1 source page carrying its anchors + a concept page);
  * zero-anchor source pages are DROPPED (cite >=1 anchor per page);
  * the concept page is dropped when NO anchor survives;
  * nothing is written outside genesis/out/ (a malicious slug can't escape);
  * the ratify-path entry point does NOT auto-apply an unratified proposal.

Run: ``/usr/bin/python3 -m pytest -q`` in this directory.
"""

from __future__ import annotations

import json
import os

import pytest

import agent_wiki_builder as awb
from agent_wiki_builder import (
    build_agent_wiki,
    build_wiki_for_ratified_proposal,
)
from genesis_contracts import (
    Anchor,
    EgressGate,
    Event,
    InMemoryCorpus,
    new_proposal,
)


# --------------------------------------------------------------------------- #
# Stub LLM — canned JSON, records calls (so we can assert egress-guarded use). #
# --------------------------------------------------------------------------- #


class StubLLM:
    """Returns canned JSON keyed on the prompt's system role. Deterministic."""

    def __init__(self, *, source_json: str | None = None, concept_json: str | None = None):
        self._source = source_json if source_json is not None else json.dumps(
            {"what_it_is": "A pricing thread.", "known": ["list_price = 5200"]}
        )
        self._concept = concept_json if concept_json is not None else json.dumps(
            {"summary": "The GTM domain centers on pricing.", "themes": ["pricing"]}
        )
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        self.calls.append((system, user))
        if "synthesize" in system:
            return self._concept
        return self._source


def _corpus() -> InMemoryCorpus:
    return InMemoryCorpus(
        [
            Event("e3", "2026-06-19T08:00:00Z", "email",
                  "list_price = 4900", "pricing-thread", "msg7"),
            Event("e4", "2026-06-21T08:00:00Z", "meeting",
                  "list_price = 5200", "pricing-review", "L8"),
            Event("e5", "2026-06-20T10:00:00Z", "email",
                  "customer onboarding for the first sales deal", "cust-thread", "m2"),
        ]
    )


def _agent_proposal(*, status: str = "proposed"):
    """A roster-style 'agent' proposal with 3 distinct anchors (3 source docs)."""
    anchors = (
        Anchor("pricing-thread", "email", "msg7"),
        Anchor("pricing-review", "meeting", "L8"),
        Anchor("cust-thread", "email", "m2"),
    )
    p = new_proposal(
        type="agent",
        confidence="medium",
        rationale="Pricing + customer threads recur; warrants a GTM lead.",
        source_anchors=anchors,
        payload={"slug": "gtm-lead", "domain": "go-to-market"},
    )
    # new_proposal forces status='proposed'; override for the ratified-path tests.
    if status != "proposed":
        import dataclasses
        p = dataclasses.replace(p, status=status)
    return p


@pytest.fixture()
def wiki_root(tmp_path, monkeypatch):
    """Point the builder's WIKI_ROOT at a temp dir (no pollution of genesis/out/)."""
    root = str(tmp_path / "wiki")
    monkeypatch.setattr(awb, "WIKI_ROOT", root)
    return root


# --------------------------------------------------------------------------- #
# Case 1 — a proposed agent WITH sources gets a wiki with CITED pages.         #
# --------------------------------------------------------------------------- #


def test_proposed_agent_gets_a_wiki_with_cited_pages(wiki_root):
    p = _agent_proposal()
    res = build_agent_wiki(
        p.payload["slug"],
        p.source_anchors,
        _corpus(),
        StubLLM(),
        EgressGate(),
        domain=p.payload["domain"],
        proposal_status=p.status,
        today="2026-06-22",
    )

    # the four schema artifacts exist
    assert os.path.isfile(res.index_path)
    assert os.path.isfile(res.log_path)
    assert len(res.source_pages) == 3          # one cited page per source doc
    assert len(res.concept_pages) == 1         # a concept synthesis page
    for path in res.source_pages + res.concept_pages:
        assert os.path.isfile(path)

    # each source page CARRIES its anchors (cited) + is UNVERIFIED-tier (de-draft)
    page = open(res.source_pages[0], encoding="utf-8").read()
    assert "## Source anchors (citations)" in page
    assert "🟡 UNVERIFIED" in page
    assert "`pricing-thread#msg7`" in page or "`pricing-review#L8`" in page \
        or "`cust-thread#m2`" in page

    # the index is now the CONCEPT ROUTER table (route -> concept -> source docs),
    # carrying the agent slug + the per-concept row, and still DRAFT/proposed.
    index = open(res.index_path, encoding="utf-8").read()
    assert "Concept Index — route here first" in index
    assert "| Concept | State" in index            # the router table header
    assert "domain-overview" in index              # the concept's row
    assert "concepts/domain-overview.md §overview" in index  # the overview link
    assert "gtm-lead" in index
    assert "proposal_status: proposed" in index    # ledger frontmatter preserved
    assert "🟡 DRAFT" in index                      # nothing ratified

    # the concept page is now a STATE file (recurrent-state + overview + source
    # docs), cited (>=1 anchor) and tier-stamped UNVERIFIED (machine-built).
    concept = open(res.concept_pages[0], encoding="utf-8").read()
    assert "## RECURRENT STATE" in concept
    assert "## HIGH-LEVEL OVERVIEW" in concept
    assert "## SOURCE DOCS — read THESE" in concept
    assert "🟡 UNVERIFIED" in concept              # the recurrent-state tier token

    # the log records the build, append-only, DRAFT, with the anchor count
    log = open(res.log_path, encoding="utf-8").read()
    assert "wiki log" in log
    assert "build |" in log
    assert "anchors carried: 3" in log


def test_every_page_cites_at_least_one_anchor(wiki_root):
    """The bar: cite >=1 anchor per page. No page may ship anchor-less."""
    p = _agent_proposal()
    res = build_agent_wiki(
        p.payload["slug"], p.source_anchors, _corpus(), StubLLM(), EgressGate(),
        domain=p.payload["domain"],
    )
    # every source + concept page contains at least one backtick anchor of the
    # form `source#locator` or `source` (kind).
    for path in res.source_pages + res.concept_pages:
        text = open(path, encoding="utf-8").read()
        assert "`pricing-thread" in text or "`pricing-review" in text \
            or "`cust-thread" in text, f"page has no citation: {path}"


# --------------------------------------------------------------------------- #
# Case 2 — zero-anchor pages are dropped.                                      #
# --------------------------------------------------------------------------- #


def test_zero_anchor_source_is_dropped(wiki_root):
    """A source whose anchor has an empty source_id can't be cited -> dropped;
    the good source still produces a page."""
    anchors = (
        Anchor("pricing-thread", "email", "msg7"),   # good
        Anchor("", "email", "ghost"),                # no source_id -> uncitable
    )
    res = build_agent_wiki(
        "gtm-lead", anchors, _corpus(), StubLLM(), EgressGate(), domain="go-to-market"
    )
    assert len(res.source_pages) == 1            # only the citable source
    assert "" in res.dropped_sources            # the empty-source_id one was dropped
    # the surviving page is the good one
    assert "pricing-thread" in open(res.source_pages[0], encoding="utf-8").read()


def test_concept_page_dropped_when_no_anchor_survives(wiki_root):
    """If EVERY source is uncitable, no source pages AND no concept page are
    written (cite >=1 per page) — but index + log still exist (the catalog)."""
    anchors = (
        Anchor("", "email", "ghost1"),
        Anchor("", "meeting", "ghost2"),
    )
    res = build_agent_wiki(
        "empty-agent", anchors, _corpus(), StubLLM(), EgressGate()
    )
    assert res.source_pages == []
    assert res.concept_pages == []               # no anchor survived -> no concept page
    assert len(res.dropped_sources) >= 1
    # index + log still written (they ARE the agent's ledger); the router shows
    # the "no concepts" fallback row (no concept survived to route to).
    assert os.path.isfile(res.index_path)
    assert os.path.isfile(res.log_path)
    index = open(res.index_path, encoding="utf-8").read()
    assert "no concepts derived yet" in index


# --------------------------------------------------------------------------- #
# Case 3 — nothing is written outside genesis/out/.                            #
# --------------------------------------------------------------------------- #


def test_all_writes_land_under_the_wiki_root(wiki_root):
    p = _agent_proposal()
    res = build_agent_wiki(
        p.payload["slug"], p.source_anchors, _corpus(), StubLLM(), EgressGate(),
        domain=p.payload["domain"],
    )
    real_root = os.path.realpath(wiki_root)
    for path in [res.index_path, res.log_path, *res.source_pages, *res.concept_pages]:
        assert os.path.realpath(path).startswith(real_root + os.sep), \
            f"escaped wiki root: {path}"


def test_wiki_root_is_under_genesis_out():
    """The REAL (un-monkeypatched) wiki root is confined under genesis/out/."""
    from genesis_pipeline import OUT_DIR
    assert os.path.realpath(awb.WIKI_ROOT).startswith(
        os.path.realpath(OUT_DIR) + os.sep
    )


def test_malicious_slug_cannot_escape_wiki_root(wiki_root):
    """A path-traversal slug ('../../etc') must be refused, not written outside.

    The slugifier collapses the dots; even if it didn't, _assert_under_wiki_root
    is the backstop. Either way: nothing lands outside the root."""
    res = build_agent_wiki(
        "../../../etc/evil", (Anchor("pricing-thread", "email", "msg7"),),
        _corpus(), StubLLM(), EgressGate(),
    )
    real_root = os.path.realpath(wiki_root)
    assert os.path.realpath(res.wiki_dir).startswith(real_root + os.sep)
    for path in [res.index_path, res.log_path, *res.source_pages]:
        assert os.path.realpath(path).startswith(real_root + os.sep)


# --------------------------------------------------------------------------- #
# Egress — the foreign-model prompt is guarded; a secret raises.               #
# --------------------------------------------------------------------------- #


def test_secret_in_corpus_text_raises_on_egress(wiki_root):
    """If a source's corpus text carries a secret, the egress guard fires before
    it can reach the model (the prompt is built from corpus excerpts)."""
    corpus = InMemoryCorpus([
        Event("e9", "2026-06-21T08:00:00Z", "email",
              "api_key = sk-ABCDEF0123456789ABCD", "leaky-thread", "msg1"),  # pragma: allowlist secret
    ])
    anchors = (Anchor("leaky-thread", "email", "msg1"),)
    from genesis_contracts import PrivateDataEgressError
    with pytest.raises(PrivateDataEgressError):
        build_agent_wiki("gtm-lead", anchors, corpus, StubLLM(), EgressGate())


def test_llm_prompts_are_egress_guarded(wiki_root):
    """Sanity: the builder DID route prompts through the model (so the egress
    guard sits in the live path), and the recorded prompts carry no secret."""
    stub = StubLLM()
    p = _agent_proposal()
    build_agent_wiki(
        p.payload["slug"], p.source_anchors, _corpus(), stub, EgressGate(),
        domain=p.payload["domain"],
    )
    assert stub.calls, "the builder never called the model"
    for _system, user in stub.calls:
        assert "sk-" not in user  # nothing secret-shaped reached the model


# --------------------------------------------------------------------------- #
# The ratify-path seam — does NOT auto-apply an unratified proposal.           #
# --------------------------------------------------------------------------- #


def test_ratify_path_refuses_unratified_proposal(wiki_root):
    """A still-'proposed' proposal is refused by default (no auto-apply)."""
    p = _agent_proposal(status="proposed")
    with pytest.raises(ValueError, match="non-ratified"):
        build_wiki_for_ratified_proposal(p, _corpus(), StubLLM(), EgressGate())
    # and nothing was written
    assert not os.path.isdir(os.path.join(wiki_root, "gtm-lead"))


def test_ratify_path_builds_for_a_ratified_proposal(wiki_root):
    """A 'ratified' agent proposal stands the agent up: builds its DRAFT wiki."""
    p = _agent_proposal(status="ratified")
    res = build_wiki_for_ratified_proposal(
        p, _corpus(), StubLLM(), EgressGate(), today="2026-06-22"
    )
    assert os.path.isfile(res.index_path)
    assert len(res.source_pages) == 3
    assert len(res.concept_pages) == 1
    # the log records that it was built from a RATIFIED proposal
    assert "proposal status at build: ratified" in open(
        res.log_path, encoding="utf-8"
    ).read()


def test_ratify_path_preview_mode_allows_proposed(wiki_root):
    """require_ratified=False builds a DRAFT *preview* for a 'proposed' proposal
    (still DRAFT, still applies nothing)."""
    p = _agent_proposal(status="proposed")
    res = build_wiki_for_ratified_proposal(
        p, _corpus(), StubLLM(), EgressGate(), require_ratified=False
    )
    assert os.path.isfile(res.index_path)
    assert "🟡 DRAFT" in open(res.index_path, encoding="utf-8").read()


def test_ratify_path_rejects_non_agent_proposal(wiki_root):
    p = new_proposal(
        type="meta_initiative", confidence="high", rationale="x",
        source_anchors=(Anchor("s", "email", "L1"),), payload={"title": "T"},
    )
    with pytest.raises(ValueError, match="agent"):
        build_wiki_for_ratified_proposal(p, _corpus(), StubLLM(), EgressGate())


def test_ratify_path_rejects_unanchored_proposal(wiki_root):
    import dataclasses
    p = _agent_proposal(status="ratified")
    p = dataclasses.replace(p, source_anchors=())
    with pytest.raises(ValueError, match="no source anchors"):
        build_wiki_for_ratified_proposal(p, _corpus(), StubLLM(), EgressGate())
