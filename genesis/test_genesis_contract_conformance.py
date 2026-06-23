"""Contract-conformance tests for the genesis claim shape (LANE L1+L2).

This is the test that was MISSING — its absence let a deprecated claim shape
ship (singular ``source_anchor``, ``recency``/``current|stale`` only, a
``conflict_status`` that emitted the non-doctrine value ``"current"``). It pins
the genesis ``Claim`` and everything the resolver/pipeline EMITS to the current
canonical doctrine contract from the brain
(``generated-pillar-projections-v1.md`` "## Claim Contract"):

    claim_id, source_anchors (PLURAL list of {path, anchor}), asserted_by
    (list of slugs), observed_at, last_evidence_change_at, confidence,
    recency_status (current|stale|unknown), conflict_status
    (aligned|disputed|superseded).

It asserts:
  * a resolved/emitted claim exposes EXACTLY those 8 doctrine-contract fields;
  * ``conflict_status`` only ever takes ``aligned|disputed|superseded``;
  * ``recency_status`` supports ``current|stale|unknown``.

Run: ``pytest -q`` in this directory.
"""

from __future__ import annotations

import dataclasses

from genesis_contracts import EgressGate, Event, InMemoryCorpus
from genesis_pipeline import _claim_from_event, run_genesis
from genesis_resolver import (
    CONFLICT_STATUS_VALUES,
    DOCTRINE_CONTRACT_FIELDS,
    RECENCY_STATUS_VALUES,
    Claim,
    resolve_claims,
)


# --------------------------------------------------------------------------- #
# The exact 8 fields the current doctrine contract requires.                   #
# --------------------------------------------------------------------------- #

_EXPECTED_CONTRACT_FIELDS = {
    "claim_id",
    "source_anchors",
    "asserted_by",
    "observed_at",
    "last_evidence_change_at",
    "confidence",
    "recency_status",
    "conflict_status",
}

# Fields the spec explicitly says the CURRENT contract does NOT carry (legacy /
# quarantined). None of these may exist on the Claim dataclass.
_FORBIDDEN_LEGACY_FIELDS = {
    "source_lane",
    "recency",
    "category",
    "deadline",
    "source_anchor",   # singular — replaced by plural source_anchors
}


def _contract_view(c: Claim) -> dict:
    """The doctrine-contract projection of a claim: exactly the 8 contract
    fields (this is what would ride into the generated pillar sections)."""
    return {name: getattr(c, name) for name in DOCTRINE_CONTRACT_FIELDS}


def _make(cid: str, **over) -> Claim:
    base = dict(
        claim_id=cid,
        source_anchors=({"path": cid, "anchor": "L1"},),
        asserted_by=(),
        observed_at="2026-06-20T12:00:00Z",
        last_evidence_change_at="2026-06-20T12:00:00Z",
        confidence="high",
        recency_status="current",
        conflict_status="aligned",
    )
    base.update(over)
    return Claim(**base)


# --------------------------------------------------------------------------- #
# 1. The contract field set                                                    #
# --------------------------------------------------------------------------- #


def test_doctrine_contract_fields_constant_matches_spec():
    # The module's declared contract tuple IS the 8-field doctrine contract.
    assert set(DOCTRINE_CONTRACT_FIELDS) == _EXPECTED_CONTRACT_FIELDS
    assert len(DOCTRINE_CONTRACT_FIELDS) == 8


def test_claim_carries_every_contract_field():
    field_names = {f.name for f in dataclasses.fields(Claim)}
    missing = _EXPECTED_CONTRACT_FIELDS - field_names
    assert not missing, f"Claim is missing contract fields: {missing}"


def test_claim_does_not_carry_legacy_fields():
    field_names = {f.name for f in dataclasses.fields(Claim)}
    leaked = _FORBIDDEN_LEGACY_FIELDS & field_names
    assert not leaked, f"deprecated legacy fields leaked back onto Claim: {leaked}"


def test_contract_view_has_exactly_eight_fields():
    c = _make("c")
    view = _contract_view(c)
    assert set(view.keys()) == _EXPECTED_CONTRACT_FIELDS
    assert len(view) == 8


def test_source_anchors_is_plural_list_of_path_anchor():
    c = _make(
        "c",
        source_anchors=(
            {"path": "pillars/x/current-state.md", "anchor": "L42"},
            {"path": "ops/exchange/y.json", "anchor": "msg7"},
        ),
    )
    assert isinstance(c.source_anchors, tuple)
    assert len(c.source_anchors) == 2
    for a in c.source_anchors:
        assert set(a.keys()) >= {"path", "anchor"}


def test_asserted_by_is_a_list_of_slugs():
    c = _make("c", asserted_by=("operator", "vendor-acme"))
    assert isinstance(c.asserted_by, tuple)
    assert list(c.asserted_by) == ["operator", "vendor-acme"]


# --------------------------------------------------------------------------- #
# 2. conflict_status only ever takes aligned|disputed|superseded               #
# --------------------------------------------------------------------------- #


def test_conflict_status_enum_is_the_doctrine_triad():
    assert CONFLICT_STATUS_VALUES == {"aligned", "disputed", "superseded"}
    # the deprecated values must NOT be members
    assert "none" not in CONFLICT_STATUS_VALUES
    assert "current" not in CONFLICT_STATUS_VALUES


def test_every_resolved_and_archived_claim_uses_only_triad_values():
    # Build a corpus exercising ALL three outcomes in one resolve:
    #   * cross-tier supersession -> winner "aligned", loser "superseded"
    #   * same-tier clash         -> winner "disputed"
    claims = [
        # cross-tier on fact "k1": operator beats secondary
        _make("op", fact_key="k1", fact_value="A", provenance_tier="operator",
              observed_at="2026-06-21T00:00:00Z"),
        _make("sec", fact_key="k1", fact_value="B", provenance_tier="secondary",
              observed_at="2026-06-19T00:00:00Z"),
        # same-tier on fact "k2": two primaries clash
        _make("p1", fact_key="k2", fact_value="X", provenance_tier="primary",
              observed_at="2026-06-20T00:00:00Z"),
        _make("p2", fact_key="k2", fact_value="Y", provenance_tier="primary",
              observed_at="2026-06-21T00:00:00Z"),
        # a context claim (no fact_key) passes through "aligned"
        _make("note"),
    ]
    result = resolve_claims(claims)

    statuses = {c.conflict_status for c in result.kept}
    statuses |= {a.claim.conflict_status for a in result.archived}
    assert statuses, "expected at least one resolved/archived claim"
    assert statuses <= CONFLICT_STATUS_VALUES, (
        f"a non-doctrine conflict_status leaked: {statuses - CONFLICT_STATUS_VALUES}"
    )
    # and specifically: all three outcomes are represented
    assert "aligned" in statuses
    assert "disputed" in statuses
    assert "superseded" in statuses


def test_pipeline_emitted_claims_use_only_triad_values():
    events = [
        Event("e1", "2026-06-18T09:00:00Z", "decision",
              "launch_date = 2026-10-15", "standup", "L1",
              meta={"asserted_by": "operator"}),
        Event("e2", "2026-06-10T12:00:00Z", "web",
              "launch_date = 2026-09-01", "partner-blog", "p3"),
        Event("e3", "2026-06-19T08:00:00Z", "email",
              "list_price = 4900", "pricing-thread", "msg7"),
    ]
    packet = run_genesis(
        InMemoryCorpus(events), roster=[], since="inception",
        llm=_NullLLM(), egress=EgressGate(), write_drafts=False,
    )
    emitted = [c for p in packet.pillars.values() for c in p.claims]
    assert emitted, "expected the pipeline to emit some resolved claims"
    for c in emitted:
        assert c.conflict_status in CONFLICT_STATUS_VALUES
        assert set(_contract_view(c).keys()) == _EXPECTED_CONTRACT_FIELDS


# --------------------------------------------------------------------------- #
# 3. recency_status supports current|stale|unknown                             #
# --------------------------------------------------------------------------- #


def test_recency_status_enum_supports_three_values():
    assert RECENCY_STATUS_VALUES == {"current", "stale", "unknown"}


def test_claim_accepts_each_recency_status_value():
    for value in ("current", "stale", "unknown"):
        c = _make("c", recency_status=value)
        assert c.recency_status == value
        # the resolver passes a no-fact_key claim through untouched, preserving it
        result = resolve_claims([c])
        assert result.kept[0].recency_status == value


# --------------------------------------------------------------------------- #
# A null LLM for the pipeline conformance run (no MI/roster proposals needed).  #
# --------------------------------------------------------------------------- #


class _NullLLM:
    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        return "[]"


# --------------------------------------------------------------------------- #
# _claim_from_event conformance (the pipeline's projection seam)               #
# --------------------------------------------------------------------------- #


def test_claim_from_event_populates_the_contract():
    ev = Event(
        "e1", "2026-06-20T10:00:00Z", "email", "list_price = 5200",
        "pricing-thread", "msg7", meta={"asserted_by": "operator"},
    )
    c = _claim_from_event(ev, category="gtm")

    # plural source_anchors of {path, anchor}
    assert isinstance(c.source_anchors, tuple) and len(c.source_anchors) == 1
    assert c.source_anchors[0]["path"] == "pricing-thread"
    assert c.source_anchors[0]["anchor"] == "msg7"
    # asserted_by is a list of slugs
    assert c.asserted_by == ("operator",)
    # last_evidence_change_at populated (defaults to the event time)
    assert c.last_evidence_change_at == "2026-06-20T10:00:00Z"
    # recency_status (not legacy "recency")
    assert c.recency_status in RECENCY_STATUS_VALUES
    # conflict_status starts at the doctrine "aligned", never legacy "none"
    assert c.conflict_status == "aligned"
    # the contract view is exactly the 8 fields
    assert set(_contract_view(c).keys()) == _EXPECTED_CONTRACT_FIELDS
