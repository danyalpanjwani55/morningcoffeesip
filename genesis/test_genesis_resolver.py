"""Tests for the genesis claim resolver (BUILD-SPEC-01 §5).

Every case in the spec's table is a test below, with explicit asserts.
Run: ``pytest -q`` in this directory.
"""

from __future__ import annotations

import dataclasses

from genesis_resolver import (
    Claim,
    TIER_RANK,
    resolve_claims,
    tier_from_item,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def make_claim(
    claim_id: str,
    *,
    fact_key: str | None = None,
    fact_value: str | None = None,
    provenance_tier: str = "secondary",
    observed_at: str = "2026-06-20T12:00:00Z",
    summary: str = "",
    conflict_status: str = "none",
    omit_tier: bool = False,
) -> Claim:
    """Build a Claim with sensible defaults for the unspecified contract fields.

    ``omit_tier=True`` constructs a claim whose ``provenance_tier`` is the
    dataclass default ("secondary") to model a 'missing tier' input (case 6).
    """
    kwargs = dict(
        claim_id=claim_id,
        category="test",
        summary=summary or claim_id,
        observed_at=observed_at,
        source_lane="test",
        source_anchor={"doc": claim_id},
        confidence="high",
        recency="current",
        conflict_status=conflict_status,
        fact_key=fact_key,
        fact_value=fact_value,
    )
    if not omit_tier:
        kwargs["provenance_tier"] = provenance_tier
    return Claim(**kwargs)


def _kept_by_value(result, value):
    return [c for c in result.kept if c.fact_value == value]


def _archived_ids(result):
    return {a.claim.claim_id for a in result.archived}


# --------------------------------------------------------------------------- #
# Case 1 — no fact_key -> all pass through unchanged, nothing archived         #
# --------------------------------------------------------------------------- #


def test_case1_no_fact_key_passthrough():
    a = make_claim("a")
    b = make_claim("b")
    c = make_claim("c")
    result = resolve_claims([a, b, c])

    assert result.kept == [a, b, c]          # identity + order preserved
    assert result.archived == []
    # untouched (no conflict_status rewrite)
    assert all(k.conflict_status == "none" for k in result.kept)


# --------------------------------------------------------------------------- #
# Case 2 — operator(newer,"A") vs secondary(older,"B") -> operator current     #
# --------------------------------------------------------------------------- #


def test_case2_operator_supersedes_secondary():
    op = make_claim(
        "op", fact_key="k", fact_value="A",
        provenance_tier="operator", observed_at="2026-06-21T00:00:00Z",
    )
    sec = make_claim(
        "sec", fact_key="k", fact_value="B",
        provenance_tier="secondary", observed_at="2026-06-19T00:00:00Z",
    )
    result = resolve_claims([op, sec])

    assert len(result.kept) == 1
    winner = result.kept[0]
    assert winner.claim_id == "op"
    assert winner.fact_value == "A"
    assert winner.conflict_status == "current"

    assert len(result.archived) == 1
    arch = result.archived[0]
    assert arch.claim.claim_id == "sec"
    assert arch.reason == "superseded"
    assert arch.superseded_by == "op"


# --------------------------------------------------------------------------- #
# Case 3 — primary "A"(older) vs primary "B"(newer) -> disputed, none archived #
# --------------------------------------------------------------------------- #


def test_case3_same_tier_dispute():
    a = make_claim(
        "a", fact_key="k", fact_value="A",
        provenance_tier="primary", observed_at="2026-06-20T00:00:00Z",
    )
    b = make_claim(
        "b", fact_key="k", fact_value="B",
        provenance_tier="primary", observed_at="2026-06-21T00:00:00Z",
    )
    result = resolve_claims([a, b])

    assert len(result.kept) == 1
    winner = result.kept[0]
    assert winner.claim_id == "b"            # newer wins the surface slot
    assert winner.conflict_status == "disputed"
    # the loser is preserved inside competing_claims (not archived)
    comp_values = {c["fact_value"] for c in winner.competing_claims}
    assert comp_values == {"A"}
    assert result.archived == []


# --------------------------------------------------------------------------- #
# Case 4 — operator "A" + primary "B" + secondary "C" -> operator current      #
# --------------------------------------------------------------------------- #


def test_case4_three_distinct_cross_tier():
    op = make_claim("op", fact_key="k", fact_value="A", provenance_tier="operator")
    pri = make_claim("pri", fact_key="k", fact_value="B", provenance_tier="primary")
    sec = make_claim("sec", fact_key="k", fact_value="C", provenance_tier="secondary")
    result = resolve_claims([op, pri, sec])

    assert len(result.kept) == 1
    winner = result.kept[0]
    assert winner.claim_id == "op"
    assert winner.conflict_status == "current"

    assert _archived_ids(result) == {"pri", "sec"}
    assert all(a.reason == "superseded" for a in result.archived)
    assert all(a.superseded_by == "op" for a in result.archived)


# --------------------------------------------------------------------------- #
# Case 5 — operator "A" + operator "B" (both operator) -> disputed             #
# --------------------------------------------------------------------------- #


def test_case5_both_operator_dispute():
    a = make_claim(
        "a", fact_key="k", fact_value="A",
        provenance_tier="operator", observed_at="2026-06-20T00:00:00Z",
    )
    b = make_claim(
        "b", fact_key="k", fact_value="B",
        provenance_tier="operator", observed_at="2026-06-21T00:00:00Z",
    )
    result = resolve_claims([a, b])

    assert len(result.kept) == 1
    winner = result.kept[0]
    assert winner.claim_id == "b"            # newer operator surfaces
    assert winner.conflict_status == "disputed"
    comp_values = {c["fact_value"] for c in winner.competing_claims}
    assert comp_values == {"A"}
    assert result.archived == []


# --------------------------------------------------------------------------- #
# Case 6 — missing provenance_tier treated as secondary -> operator wins       #
# --------------------------------------------------------------------------- #


def test_case6_missing_tier_treated_secondary():
    # 'missing' = the dataclass default ("secondary").
    missing = make_claim(
        "missing", fact_key="k", fact_value="B", omit_tier=True,
        observed_at="2026-06-21T00:00:00Z",
    )
    assert missing.provenance_tier == "secondary"  # default applied
    op = make_claim(
        "op", fact_key="k", fact_value="A",
        provenance_tier="operator", observed_at="2026-06-19T00:00:00Z",
    )
    result = resolve_claims([missing, op])

    assert len(result.kept) == 1
    winner = result.kept[0]
    assert winner.claim_id == "op"
    assert winner.conflict_status == "current"
    assert _archived_ids(result) == {"missing"}
    assert result.archived[0].reason == "superseded"


# --------------------------------------------------------------------------- #
# Case 7 — same fact_key, same fact_value (agreement) -> one kept, one dup     #
# --------------------------------------------------------------------------- #


def test_case7_agreement_dedup_not_disputed():
    a = make_claim(
        "a", fact_key="k", fact_value="SAME",
        provenance_tier="primary", observed_at="2026-06-20T00:00:00Z",
    )
    b = make_claim(
        "b", fact_key="k", fact_value="SAME",
        provenance_tier="primary", observed_at="2026-06-21T00:00:00Z",
    )
    result = resolve_claims([a, b])

    assert len(result.kept) == 1
    winner = result.kept[0]
    assert winner.fact_value == "SAME"
    # newest representative kept; conflict_status NOT promoted to disputed
    assert winner.claim_id == "b"
    assert winner.conflict_status != "disputed"
    assert winner.conflict_status == "none"

    assert len(result.archived) == 1
    arch = result.archived[0]
    assert arch.claim.claim_id == "a"
    assert arch.reason == "duplicate_value"
    assert arch.superseded_by == "b"


# --------------------------------------------------------------------------- #
# Case 8 — mixed: a no-fact_key claim + a cross-tier pair, one call            #
# --------------------------------------------------------------------------- #


def test_case8_passthrough_and_pair_in_one_call():
    note = make_claim("note")                              # no fact_key
    op = make_claim("op", fact_key="k", fact_value="A", provenance_tier="operator",
                    observed_at="2026-06-21T00:00:00Z")
    sec = make_claim("sec", fact_key="k", fact_value="B", provenance_tier="secondary",
                     observed_at="2026-06-19T00:00:00Z")
    result = resolve_claims([note, op, sec])

    kept_ids = [c.claim_id for c in result.kept]
    assert "note" in kept_ids                              # passthrough preserved
    note_kept = next(c for c in result.kept if c.claim_id == "note")
    assert note_kept is note                               # untouched identity

    op_kept = next(c for c in result.kept if c.claim_id == "op")
    assert op_kept.conflict_status == "current"
    assert _archived_ids(result) == {"sec"}
    assert result.archived[0].reason == "superseded"


# --------------------------------------------------------------------------- #
# Determinism / purity / contract guards                                       #
# --------------------------------------------------------------------------- #


def test_determinism_same_input_same_output():
    claims = [
        make_claim("op", fact_key="k", fact_value="A", provenance_tier="operator"),
        make_claim("p", fact_key="k", fact_value="B", provenance_tier="primary"),
        make_claim("s", fact_key="k", fact_value="C", provenance_tier="secondary"),
        make_claim("note"),
    ]
    r1 = resolve_claims(list(claims))
    r2 = resolve_claims(list(claims))
    assert r1 == r2                                        # dataclasses compare by value


def test_input_not_mutated():
    op = make_claim("op", fact_key="k", fact_value="A", provenance_tier="operator")
    sec = make_claim("sec", fact_key="k", fact_value="B", provenance_tier="secondary")
    before = (op, sec)
    resolve_claims([op, sec])
    # frozen dataclasses can't be mutated, but assert the originals are intact
    assert op.conflict_status == "none"
    assert sec.conflict_status == "none"
    assert before == (op, sec)


def test_claim_is_frozen():
    c = make_claim("x")
    try:
        c.summary = "mutated"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("Claim must be frozen")


def test_claim_contract_field_order_preserved():
    names = [f.name for f in dataclasses.fields(Claim)]
    # Original recovered contract order, then the two appended provenance fields.
    assert names == [
        "claim_id", "category", "summary", "observed_at", "source_lane",
        "source_anchor", "confidence", "recency", "conflict_status",
        "participants", "owner", "deadline", "competing_claims",
        "fact_key", "fact_value",
        "provenance_tier", "asserted_by",
    ]


# --------------------------------------------------------------------------- #
# tier_from_item                                                               #
# --------------------------------------------------------------------------- #


def test_tier_from_item_operator_lane():
    assert tier_from_item({"source_lane": "decision"}) == "operator"
    assert tier_from_item({"source": "proposed_update"}) == "operator"


def test_tier_from_item_operator_asserted():
    assert tier_from_item({"source_lane": "web", "asserted_by": "operator"}) == "operator"


def test_tier_from_item_primary_lanes():
    for lane in ("email", "meeting", "calendar", "gmail", "transcript"):
        assert tier_from_item({"source_lane": lane}) == "primary"


def test_tier_from_item_default_secondary():
    assert tier_from_item({"source_lane": "web"}) == "secondary"
    assert tier_from_item({}) == "secondary"


def test_tier_from_item_explicit_tier_honored():
    assert tier_from_item({"provenance_tier": "primary", "source_lane": "web"}) == "primary"
    # invalid explicit tier falls back to lane logic
    assert tier_from_item({"provenance_tier": "bogus", "source_lane": "email"}) == "primary"


def test_tier_from_item_action_item_owner_authored():
    # action item authored by the owner -> operator's own word
    assert (
        tier_from_item(
            {"source_lane": "action_item", "owner": "owner_a", "asserted_by": "owner_a"}
        )
        == "operator"
    )
    # action item authored by someone else -> primary, not operator
    assert (
        tier_from_item(
            {"source_lane": "action_item", "owner": "owner_a", "asserted_by": "owner_b"}
        )
        == "primary"
    )


def test_tier_rank_values():
    assert TIER_RANK == {"operator": 3, "primary": 2, "secondary": 1}
