"""Tests for Engine 3 — the agent-router (2.4).

Pins the spec's bar:
  * single-domain doc -> one owner;
  * cross-cutting doc -> BOTH owners (co-own within margin);
  * unroutable doc -> status="unassigned" (HELD — NOT mis-filed);
  * the engine-1 candidates CONSTRAIN the winner (an agent absent from the
    candidate set can't win even if its keywords match);
  * a 3+ way tie -> all co-own;
  * coordinator/operator can never own (no catch-all mis-file).

Deterministic; stdlib only; no network.
"""

from __future__ import annotations

from ingest.train_on_docs.relevance import AgentDomain
from ingest.train_on_docs.router import RoutableDoc, route_doc


PRODUCT = AgentDomain("specialist-product", "product",
                      ("product", "feature", "roadmap", "design"))
GTM = AgentDomain("growth-marketing", "gtm",
                  ("pricing", "sales", "market", "campaign"))
LEGAL = AgentDomain("specialist-legal-business", "operations",
                    ("contract", "legal", "compliance", "patent"))
TABLE = [PRODUCT, GTM, LEGAL]


def test_single_domain_doc_gets_one_owner():
    doc = RoutableDoc(
        doc_id="d1",
        doc_text="the product feature roadmap and design review",
        candidate_owners=("specialist-product",),
    )
    d = route_doc(doc, TABLE)
    assert d.status == "routed"
    assert d.owners == ("specialist-product",)
    assert d.pillar == "product"


def test_cross_cutting_doc_is_co_owned_by_both():
    # equal, strong overlap in product AND gtm; candidates include both.
    doc = RoutableDoc(
        doc_id="d2",
        doc_text="product feature roadmap design and pricing sales market campaign",
        candidate_owners=("specialist-product", "growth-marketing"),
    )
    d = route_doc(doc, TABLE, multi_margin=0)
    assert d.status == "routed"
    assert set(d.owners) == {"specialist-product", "growth-marketing"}
    assert len(d.owners) == 2


def test_unroutable_doc_is_held_not_misfiled():
    # On-company (it got here) but no candidate owner from relevance -> HELD.
    doc = RoutableDoc(
        doc_id="d3",
        doc_text="a friendly hello and a quick personal check-in",
        candidate_owners=(),
    )
    d = route_doc(doc, TABLE)
    assert d.status == "unassigned"
    assert d.owners == ()
    assert d.held is True


def test_top_below_min_overlap_is_held():
    # candidate present, but its keywords don't actually appear in the doc text
    # -> top overlap 0 < min_overlap 1 -> held (brand-new topic).
    doc = RoutableDoc(
        doc_id="d4",
        doc_text="something with no matching domain words whatsoever here",
        candidate_owners=("specialist-product",),
    )
    d = route_doc(doc, TABLE, min_overlap=1)
    assert d.status == "unassigned"
    assert d.owners == ()


def test_engine1_candidates_constrain_the_winner():
    # The doc text matches GTM strongly, but GTM is NOT in the candidate set;
    # only product is. The router must NOT pull in GTM (it trusts relevance).
    doc = RoutableDoc(
        doc_id="d5",
        doc_text="pricing sales market campaign — but also one product mention",
        candidate_owners=("specialist-product",),
    )
    d = route_doc(doc, TABLE)
    assert d.owners == ("specialist-product",)
    assert "growth-marketing" not in d.scores


def test_three_way_tie_all_co_own():
    doc = RoutableDoc(
        doc_id="d6",
        doc_text="product pricing contract — one term from each domain",
        candidate_owners=(
            "specialist-product", "growth-marketing", "specialist-legal-business",
        ),
    )
    d = route_doc(doc, TABLE, multi_margin=0)
    assert d.status == "routed"
    assert len(d.owners) == 3


def test_coordinator_and_operator_can_never_own():
    # Even if a candidate set names a catch-all slug, it is excluded.
    coord = AgentDomain("coordinator", "operations", ("anything", "everything"))
    doc = RoutableDoc(
        doc_id="d7",
        doc_text="anything everything product feature",
        candidate_owners=("coordinator", "operator", "specialist-product"),
    )
    d = route_doc(doc, TABLE + [coord])
    assert "coordinator" not in d.owners
    assert "operator" not in d.owners
    assert d.owners == ("specialist-product",)
