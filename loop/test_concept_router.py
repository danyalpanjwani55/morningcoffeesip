"""Tests for loop/concept_router — read-wiki-first routing (Lane B).

The contract being verified (brain §3.4, SDL-68/SDL-56):
  * a query matching a concept returns it WITH its source-docs directive (the
    routing payoff — the read-THESE list, never empty when the concept has one);
  * a no-match query returns ``status="new-concept"`` with ``concept=None`` (a
    SIGNAL, never a guessed-wrong concept);
  * routing is a pure, deterministic function of (query, index) — same inputs,
    same result, ties broken by router-row order.

Built on the real index format: rows are rendered via ``journal_schema`` so the
test exercises the same parser ``route_query`` uses (no hand-built strings that
could drift from the schema).
"""

from __future__ import annotations

from concept_router import NEW_CONCEPT, ROUTED, route_query
from journal_schema import RouterRow, render_router_index


def _index(*rows: RouterRow) -> str:
    return render_router_index("specialist-support", list(rows))


# A small, generic two-concept index (no origin-company content).
_SUPPORT = RouterRow(
    concept="customer-support",
    state="refund SLA is 48h · high",
    overview_link="concepts/customer-support.md §overview",
    source_docs="sources/02 the refund policy, sources/03 the tier map",
)
_BILLING = RouterRow(
    concept="billing",
    state="Stripe is the payment processor · high",
    overview_link="concepts/billing.md §overview",
    source_docs="sources/05 the billing runbook",
)


# --- a matching query routes + carries the source-docs directive ------------ #

def test_matching_query_routes_to_concept_with_source_docs():
    res = route_query("what is the refund SLA for a customer?", _index(_SUPPORT, _BILLING))
    assert res.status == ROUTED
    assert res.concept == "customer-support"
    # The routing payoff: the read-THESE directive, not empty.
    assert res.source_docs == _SUPPORT.source_docs
    assert res.source_docs.strip()
    # And the current-truth line came along for the ride.
    assert res.recurrent_state == _SUPPORT.state


def test_routes_to_the_more_relevant_of_several_concepts():
    res = route_query("which payment processor do we use for billing?",
                      _index(_SUPPORT, _BILLING))
    assert res.status == ROUTED
    assert res.concept == "billing"
    assert res.source_docs == _BILLING.source_docs


def test_matches_on_state_text_not_only_slug():
    """A query that quotes a phrase from the STATE line (not the slug) still
    routes — the state names the current truth, so it is part of the bank."""
    res = route_query("is Stripe set up correctly?", _index(_SUPPORT, _BILLING))
    assert res.status == ROUTED
    assert res.concept == "billing"


# --- a no-match query is a new-concept SIGNAL, never a wrong guess ----------- #

def test_no_match_returns_new_concept_signal_with_none_concept():
    res = route_query("how do I file our quarterly tax return in Germany?",
                      _index(_SUPPORT, _BILLING))
    assert res.status == NEW_CONCEPT
    assert res.concept is None
    # A miss yields no fabricated directive.
    assert res.source_docs == ""
    assert res.recurrent_state == ""


def test_empty_index_is_new_concept():
    res = route_query("anything at all", _index())
    assert res.status == NEW_CONCEPT
    assert res.concept is None


def test_empty_query_is_new_concept():
    res = route_query("   ", _index(_SUPPORT, _BILLING))
    assert res.status == NEW_CONCEPT
    assert res.concept is None


def test_only_stopwords_in_common_is_new_concept():
    """Sharing only generic glue words ("the", "is", "a") must NOT count as a
    match — that would route everything to the first row."""
    res = route_query("is it the one?", _index(_SUPPORT, _BILLING))
    assert res.status == NEW_CONCEPT
    assert res.concept is None


# --- determinism ------------------------------------------------------------ #

def test_routing_is_pure_and_deterministic():
    idx = _index(_SUPPORT, _BILLING)
    a = route_query("refund SLA question", idx)
    b = route_query("refund SLA question", idx)
    assert a == b


def test_tie_breaks_by_router_row_order():
    """Two concepts that match the query equally well -> the earlier row wins
    (deterministic, index-ordered)."""
    left = RouterRow("alpha", "shared topic word · high", "concepts/alpha.md §overview", "sources/01")
    right = RouterRow("beta", "shared topic word · high", "concepts/beta.md §overview", "sources/02")
    # Query overlaps "topic"+"word" with BOTH equally.
    res = route_query("a question about the topic word", _index(left, right))
    assert res.status == ROUTED
    assert res.concept == "alpha"  # first row wins the tie


def test_matched_terms_explain_the_route():
    res = route_query("refund policy question", _index(_SUPPORT, _BILLING))
    assert res.status == ROUTED
    assert "refund" in res.matched_terms
