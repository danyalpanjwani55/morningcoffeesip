"""Tests for ITEM 2.2 — three honest draft-tiers (de-draft doctrine).

``derive_tier`` maps a claim's contract fields (anchor presence + recency_status
+ conflict_status + confidence) to verified / unverified / aspirational — so the
confident label is reserved for what is actually confident, instead of a flat
``🟡 DRAFT`` on every page. No source-verification engine: tiers come PURELY from
the fields the contract already carries. A machine-built page (no recency/conflict
known) is UNVERIFIED, never VERIFIED — the honest default the doctrine requires.

Run: ``/usr/bin/python3 -B -m pytest -q`` in this directory.
"""

from __future__ import annotations

from agent_wiki_builder import (
    derive_tier,
    TIER_VERIFIED,
    TIER_UNVERIFIED,
    TIER_ASPIRATIONAL,
)


def test_no_anchor_is_aspirational():
    assert derive_tier(has_anchor=False) == TIER_ASPIRATIONAL
    # ...even with otherwise-perfect status: no citation => never verified.
    assert (
        derive_tier(
            has_anchor=False,
            recency_status="current",
            conflict_status="aligned",
            confidence="high",
        )
        == TIER_ASPIRATIONAL
    )


def test_cited_current_aligned_is_verified():
    assert (
        derive_tier(
            has_anchor=True,
            recency_status="current",
            conflict_status="aligned",
            confidence="high",
        )
        == TIER_VERIFIED
    )
    assert (
        derive_tier(
            has_anchor=True,
            recency_status="current",
            conflict_status="aligned",
            confidence="medium",
        )
        == TIER_VERIFIED
    )


def test_stale_or_unknown_recency_is_unverified():
    for rs in ("stale", "unknown"):
        assert (
            derive_tier(has_anchor=True, recency_status=rs, conflict_status="aligned")
            == TIER_UNVERIFIED
        )


def test_disputed_conflict_is_unverified():
    assert (
        derive_tier(
            has_anchor=True, recency_status="current", conflict_status="disputed"
        )
        == TIER_UNVERIFIED
    )


def test_low_confidence_is_unverified():
    assert (
        derive_tier(
            has_anchor=True,
            recency_status="current",
            conflict_status="aligned",
            confidence="low",
        )
        == TIER_UNVERIFIED
    )


def test_builder_default_no_status_is_unverified_never_verified():
    # The wiki builder calls derive_tier(has_anchor=True) with NO recency/conflict
    # (it has none) -> UNVERIFIED. A machine-built cited page is never VERIFIED.
    t = derive_tier(has_anchor=True)
    assert t == TIER_UNVERIFIED
    assert t != TIER_VERIFIED


def test_unanchored_claim_is_aspirational_never_verified():
    # The spec bar: an unanchored claim is aspirational, never verified.
    assert (
        derive_tier(
            has_anchor=False, recency_status="current", conflict_status="aligned"
        )
        != TIER_VERIFIED
    )
    assert derive_tier(has_anchor=False) == TIER_ASPIRATIONAL


def test_three_tiers_are_distinct():
    assert len({TIER_VERIFIED, TIER_UNVERIFIED, TIER_ASPIRATIONAL}) == 3
