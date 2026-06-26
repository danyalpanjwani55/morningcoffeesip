"""Tests for Engine 1 — the relevance-gate (2.4).

Pins the spec's bar:
  * relevant doc -> kept + the right agent is the top candidate;
  * off-topic doc (dentist / newsletter) -> dropped, reason="off_topic";
  * participant-in-allowlist -> kept despite thin domain vocab;
  * multi-agent doc -> multiple candidates (ranked);
  * empty doc -> dropped;
  * on-company-but-no-owner -> kept, candidates=(), reason="no_domain_match".

Deterministic; stdlib only; no network. Run from the repo root:
``/usr/bin/python3 -B -m pytest -q ingest/train_on_docs/``.
"""

from __future__ import annotations

from genesis_contracts import Event

from ingest.train_on_docs.relevance import AgentDomain, gate_document


# A small, explicit agent table (no company content — generic domains).
PRODUCT = AgentDomain(
    slug="specialist-product",
    pillar="product",
    keywords=("product", "feature", "roadmap", "design", "prototype"),
)
GTM = AgentDomain(
    slug="growth-marketing",
    pillar="gtm",
    keywords=("pricing", "sales", "market", "campaign", "funnel"),
)
TABLE = [PRODUCT, GTM]


def _events(*texts, source_id="doc1", kind="email", participants=()):
    return [
        Event(
            event_id=f"e{i}",
            observed_at=f"2026-06-2{i}T00:00:00Z",
            kind=kind,
            text=t,
            source_id=source_id,
            locator=f"m{i}",
            participants=tuple(participants),
        )
        for i, t in enumerate(texts, start=1)
    ]


def test_relevant_doc_is_kept_and_routes_to_the_right_agent():
    evs = _events("the product roadmap and a new feature design for launch")
    v = gate_document(evs, TABLE)
    assert v.kept is True
    assert v.on_company is True
    assert v.reason == "relevant"
    assert v.best is not None
    assert v.best.slug == "specialist-product"
    # the matched terms explain the route
    assert "product" in v.best.matched_terms


def test_off_topic_doc_is_dropped():
    # a dentist appointment — about nobody's domain, nobody allowlisted.
    evs = _events("reminder: your dentist cleaning appointment is on Tuesday at 3pm")
    v = gate_document(evs, TABLE)
    assert v.kept is False
    assert v.on_company is False
    assert v.reason == "off_topic"
    assert v.candidates == ()


def test_newsletter_off_topic_is_dropped():
    evs = _events("this week in tech: top 10 gadgets and a recipe newsletter digest")
    v = gate_document(evs, TABLE)
    assert v.kept is False
    assert v.reason == "off_topic"


def test_newsletter_dropped_against_the_REAL_default_table():
    """Regression for the substring false-positive (adversarial-review finding #1):
    against the ACTUAL shipped domain table — not a curated one — a gardening-tips
    newsletter must be dropped. Before the word-boundary fix, the short token "ip"
    in the legal-business bank matched INSIDE "tips"/"equipment"/"shops" and KEPT
    the newsletter; every curated-table test dodged the collision and hid it."""
    from ingest.train_on_docs.pipeline import default_agent_domains

    evs = _events(
        "Weekly newsletter: top ten gardening tips for spring planting, "
        "flower beds, equipment, shops, and recipe ideas"
    )
    v = gate_document(evs, default_agent_domains())
    assert v.kept is False, f"off-topic newsletter leaked in: {v.reason} {v.candidates}"
    assert v.on_company is False
    assert v.reason == "off_topic"


class _Allow:
    """Minimal Allowlist stand-in: contains_any over a known identity set."""

    def __init__(self, ids):
        self._ids = set(ids)

    def contains_any(self, identities):
        return any(i in self._ids for i in identities if i)


def test_allowlisted_participant_keeps_a_thin_vocab_doc():
    # thin domain vocab, but a participant the founder corresponds with.
    evs = _events(
        "quick note about the thing we discussed",
        participants=("alice@example.com",),
    )
    allow = _Allow({"alice@example.com"})
    v = gate_document(evs, TABLE, allowlist=allow)
    assert v.kept is True
    assert v.on_company is True
    # no domain matched -> held for the router (no_domain_match)
    assert v.candidates == ()
    assert v.reason == "no_domain_match"


def test_on_company_but_no_owner_is_kept_with_no_candidates():
    # allowlisted participant, zero domain overlap -> kept, candidates=().
    evs = _events("see you at lunch", participants=("bob@example.com",))
    allow = _Allow({"bob@example.com"})
    v = gate_document(evs, TABLE, allowlist=allow)
    assert v.kept is True
    assert v.candidates == ()
    assert v.reason == "no_domain_match"


def test_multi_agent_doc_returns_multiple_ranked_candidates():
    # mentions BOTH product and gtm vocab strongly.
    evs = _events(
        "the product feature roadmap drives our pricing and sales campaign funnel"
    )
    v = gate_document(evs, TABLE)
    assert v.kept is True
    slugs = [c.slug for c in v.candidates]
    assert "specialist-product" in slugs
    assert "growth-marketing" in slugs
    assert len(v.candidates) >= 2
    # ranked best-first: scores are non-increasing
    scores = [c.score for c in v.candidates]
    assert scores == sorted(scores, reverse=True)


def test_empty_doc_is_dropped():
    v = gate_document([], TABLE)
    assert v.kept is False
    assert v.reason == "empty"

    v2 = gate_document(_events("   "), TABLE)
    assert v2.kept is False
    assert v2.reason == "empty"
