"""Tests for the concept-routed wiki builder (LANE A — learning-loop-v2 port).

The FORMAT upgrade: ``build_agent_wiki`` now emits the agent's concept page(s) as
per-concept STATE files (``concepts/<slug>.md`` in the ``journal_schema``
``ConceptState`` shape) and rewrites ``index.md`` as the concept ROUTER table
(``render_router_index`` — one row per concept). This pins the done-bar:

  * the router ``index.md`` rows ``parse_router_index`` back to the concepts;
  * every ``concepts/<slug>.md`` round-trips through ``parse_concept_state``;
  * each concept row points at REAL source docs (anchors that correspond to the
    ``sources/`` pages actually written);
  * everything stays DRAFT / tier-stamped (proposals-only — nothing ratified).

Deterministic; stub LLM; no network. Imports ``journal_schema`` from ``loop/``
(added to ``sys.path`` the way the builder itself resolves it).

Run: ``/usr/bin/python3 -B -m pytest -q genesis/test_concept_wiki.py``.
"""

from __future__ import annotations

import json
import os
import re
import sys

import pytest

import agent_wiki_builder as awb
from agent_wiki_builder import build_agent_wiki
from genesis_contracts import Anchor, EgressGate, Event, InMemoryCorpus, new_proposal

# journal_schema lives under loop/ — resolve it the same way the builder does.
_LOOP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "loop")
if _LOOP not in sys.path:
    sys.path.insert(0, _LOOP)
from journal_schema import (  # noqa: E402
    SchemaError,
    parse_concept_state,
    parse_router_index,
)

# Draft / tier tokens the wiki stamps (proposals-only invariant).
DRAFT = "🟡 DRAFT"
TIER_TOKENS = ("🟢 VERIFIED", "🟡 UNVERIFIED", "⚪ ASPIRATIONAL")


# --------------------------------------------------------------------------- #
# Stub LLM — canned synthesis JSON (deterministic). Mirrors the builder test.  #
# --------------------------------------------------------------------------- #


class StubLLM:
    def __init__(self, *, summary: str = "The GTM domain centers on pricing.",
                 themes=("pricing", "onboarding")):
        self._summary = summary
        self._themes = list(themes)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        self.calls.append((system, user))
        if "synthesize" in system:
            return json.dumps({"summary": self._summary, "themes": self._themes})
        return json.dumps({"what_it_is": "A pricing thread.", "known": ["list_price = 5200"]})


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


def _agent_proposal():
    anchors = (
        Anchor("pricing-thread", "email", "msg7"),
        Anchor("pricing-review", "meeting", "L8"),
        Anchor("cust-thread", "email", "m2"),
    )
    return new_proposal(
        type="agent",
        confidence="medium",
        rationale="Pricing + customer threads recur; warrants a GTM lead.",
        source_anchors=anchors,
        payload={"slug": "gtm-lead", "domain": "go-to-market"},
    )


@pytest.fixture()
def wiki_root(tmp_path, monkeypatch):
    root = str(tmp_path / "wiki")
    monkeypatch.setattr(awb, "WIKI_ROOT", root)
    return root


def _build(wiki_root):
    p = _agent_proposal()
    return build_agent_wiki(
        p.payload["slug"], p.source_anchors, _corpus(), StubLLM(), EgressGate(),
        domain=p.payload["domain"], proposal_status=p.status, today="2026-06-29",
    )


# --------------------------------------------------------------------------- #
# Done-bar 1 — index.md is a ROUTER whose rows parse back to the concepts.     #
# --------------------------------------------------------------------------- #


def test_index_is_a_concept_router_table(wiki_root):
    res = _build(wiki_root)
    index = open(res.index_path, encoding="utf-8").read()

    # the router header + the 4-column table (Foundation F's render_router_index)
    assert "route here first" in index
    assert "| Concept | State (current truth + confidence) | Overview | Source docs to read |" in index

    rows = parse_router_index(index)
    assert len(rows) == 1, "one row per concept (single domain-overview concept)"
    row = rows[0]
    assert row.concept == "domain-overview"
    # the row's 1-line state carries a tier token (current truth + confidence)
    assert any(tok in row.state for tok in TIER_TOKENS)
    # the overview link points at the concept's STATE file §overview
    assert row.overview_link == "concepts/domain-overview.md §overview"
    # the source-docs cell names where to read (the routing payoff)
    assert "pricing-thread" in row.source_docs


def test_router_row_points_at_an_existing_state_file(wiki_root):
    """Every router row's overview link resolves to a concept STATE file that was
    actually written (the route lands somewhere real)."""
    res = _build(wiki_root)
    rows = parse_router_index(open(res.index_path, encoding="utf-8").read())
    written = {os.path.basename(p) for p in res.concept_pages}
    for row in rows:
        # overview_link looks like "concepts/<slug>.md §overview"
        m = re.match(r"concepts/([^ ]+\.md)", row.overview_link)
        assert m, f"router overview link not a concepts/ path: {row.overview_link!r}"
        assert m.group(1) in written, f"router points at a missing STATE file: {m.group(1)}"


# --------------------------------------------------------------------------- #
# Done-bar 2 — each concepts/<slug>.md round-trips through parse_concept_state. #
# --------------------------------------------------------------------------- #


def test_concept_state_files_round_trip(wiki_root):
    res = _build(wiki_root)
    assert res.concept_pages, "a built wiki must have >=1 concept STATE file"
    for path in res.concept_pages:
        md = open(path, encoding="utf-8").read()
        cs = parse_concept_state(md)  # raises SchemaError if not the canonical shape
        # the parsed state carries the agent + a real slug, seeded recurrent state,
        # the synthesis overview, and the source-docs directive.
        assert cs.agent == "gtm-lead"
        assert cs.slug == "domain-overview"
        assert cs.state_updated == "2026-06-29"
        assert cs.recurrent_state, "recurrent state seeded from the concept's claims"
        assert cs.overview, "overview = the synthesis body"
        assert cs.source_docs, "source-docs directive (read THESE primaries)"
        # the recurrent-state claims are tier-stamped (de-draft confidence token)
        assert any(tok in line for line in cs.recurrent_state for tok in TIER_TOKENS)


def test_concept_state_points_at_real_source_docs(wiki_root):
    """The source-docs directive points at anchors that correspond to source
    pages the builder ACTUALLY wrote (not dangling references)."""
    res = _build(wiki_root)
    # the source_ids that got a real sources/NN_*.md page
    written_source_slugs = {
        re.sub(r"^\d+_", "", os.path.splitext(os.path.basename(p))[0])
        for p in res.source_pages
    }
    assert written_source_slugs, "expected >=1 cited source page"

    for path in res.concept_pages:
        cs = parse_concept_state(open(path, encoding="utf-8").read())
        # every source-docs line references a backtick `source_id#locator`; the
        # source_id must map to a written source page (slugified).
        for directive in cs.source_docs:
            m = re.search(r"`([^`#]+)(?:#[^`]+)?`", directive)
            assert m, f"source-docs line has no anchor: {directive!r}"
            src_slug = awb._slugify(m.group(1))
            assert src_slug in written_source_slugs, \
                f"concept points at a source with no page: {m.group(1)}"


# --------------------------------------------------------------------------- #
# Done-bar 3 — proposals-only: everything DRAFT / tier-stamped.                #
# --------------------------------------------------------------------------- #


def test_everything_is_draft_and_tier_stamped(wiki_root):
    """The router index stays DRAFT + proposal-status; the concept STATE files are
    tier-stamped. Nothing is ratified, sent, or applied (proposals-only)."""
    res = _build(wiki_root)

    index = open(res.index_path, encoding="utf-8").read()
    assert DRAFT in index, "router index keeps the DRAFT stamp (nothing ratified)"
    assert "proposal_status: proposed" in index

    for path in res.concept_pages:
        page = open(path, encoding="utf-8").read()
        assert any(tok in page for tok in TIER_TOKENS), \
            f"concept STATE file is not tier-stamped: {path}"
        # a machine-built cited concept is UNVERIFIED — never auto-promoted to VERIFIED
        assert "🟢 VERIFIED" not in page


def test_router_round_trips_when_no_concept_survives(wiki_root):
    """If every source is uncitable, no concept STATE file is written and the
    router shows the no-concepts fallback — which parses to ZERO rows (never a
    phantom concept)."""
    anchors = (Anchor("", "email", "g1"), Anchor("", "meeting", "g2"))
    res = build_agent_wiki(
        "empty-agent", anchors, _corpus(), StubLLM(), EgressGate(), today="2026-06-29"
    )
    assert res.concept_pages == []
    index = open(res.index_path, encoding="utf-8").read()
    assert parse_router_index(index) == []          # no phantom rows
    assert DRAFT in index                            # still DRAFT


def test_malformed_concept_state_never_parses_silently(wiki_root):
    """Guard the never-silent contract: a concept STATE file missing its
    frontmatter raises SchemaError, not a half-parsed object."""
    with pytest.raises(SchemaError):
        parse_concept_state("# domain-overview\n\n## RECURRENT STATE\n\n- x\n")
