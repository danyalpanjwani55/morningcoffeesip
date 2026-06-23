"""Genesis claim resolver — tier > recency > supersede (archive-don't-delete).

BUILD-SPEC-01. Pure, deterministic, stdlib-only. No I/O, no network, no
dependency on any live brain. When two facts about the same thing disagree,
this module decides which one is *current* (operator > primary > secondary;
newer wins within a tier), archives the loser (never deletes), and only marks
a genuine ``disputed`` when two facts of the SAME tier clash.

Drop-in seam (in the populator's ``claims_for_subject``)::

    resolved = resolve_claims(dedupe_claims(claims))
    return detect_conflicts(resolved.kept)   # now only ever sees same-tier ties

Run the demo::

    python genesis_resolver.py        # loads sample_claims.json, prints summary
    pytest -q                         # runs test_genesis_resolver.py
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# 1. The Claim contract (verbatim from the recovered populator) + 2 new fields #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Claim:
    """A single dated, sourced fact. Field names/order match the recovered
    upstream populator's contract EXACTLY; the two provenance
    fields are appended (keeps ``frozen=True``, keeps positional order)."""

    claim_id: str
    category: str
    summary: str
    observed_at: str            # ISO-8601 UTC, e.g. "2026-06-20T14:03:00Z"
    source_lane: str
    source_anchor: dict[str, str]
    confidence: str             # "high" | "medium" | "low"
    recency: str                # "current" | "stale"
    conflict_status: str        # "none" | "disputed" (today)
    participants: tuple[str, ...] = ()
    owner: str | None = None
    deadline: str | None = None
    competing_claims: tuple[dict[str, Any], ...] = ()
    fact_key: str | None = None    # conflicts are detected on this
    fact_value: str | None = None  # ... and this
    # --- appended by BUILD-SPEC-01 (provenance tier) ---
    provenance_tier: str = "secondary"   # "operator" | "primary" | "secondary"
    asserted_by: str | None = None       # who/what asserted it (archive note)


@dataclass(frozen=True)
class ArchivedClaim:
    """A claim the resolver retired, with why + what superseded it. The caller
    writes these to an archive section — archive-don't-delete."""

    claim: Claim
    reason: str            # "superseded" | "superseded_lower_tier" | "duplicate_value"
    superseded_by: str     # claim_id of the winner


@dataclass(frozen=True)
class ResolveResult:
    """What ``resolve_claims`` returns: the surviving current truth + the
    archived losers."""

    kept: list[Claim] = field(default_factory=list)
    archived: list[ArchivedClaim] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 2. Provenance tiering                                                        #
# --------------------------------------------------------------------------- #

# Authority order, from the brain's canonical hierarchy:
#   operator (the founder's own word) > primary (sources they're party to)
#   > secondary (third-party / inference / default).
TIER_RANK: dict[str, int] = {"operator": 3, "primary": 2, "secondary": 1}

# Explicit lane/source -> tier map (BUILD-SPEC-01 §2b). Default is "secondary".
_OPERATOR_LANES = frozenset(
    {"proposed_update", "decision", "operator", "action_item"}
)
_PRIMARY_LANES = frozenset(
    {"email", "meeting", "calendar", "gmail", "transcript"}
)


def _tier_rank(tier: str | None) -> int:
    """Rank a (possibly missing/unknown) tier; missing/unknown -> secondary (1)."""
    return TIER_RANK.get(tier or "secondary", 1)


def tier_from_item(item: dict[str, Any]) -> str:
    """Derive an authority tier for an item being turned into a Claim.

    operator > primary > secondary. An explicit ``item["provenance_tier"]`` is
    honored if valid. Otherwise the item's lane/source decides; the operator
    can also be named via ``item["asserted_by"]``/``item["owner"]`` together
    with an ``action_item`` lane (an action item authored by the owner is the
    founder's own word). Unknown -> "secondary".
    """
    # An explicitly-stamped, valid tier wins (queue-grounding may pre-stamp).
    explicit = item.get("provenance_tier")
    if explicit in TIER_RANK:
        return explicit

    lane = (item.get("source_lane") or item.get("source") or "").strip().lower()

    if lane in _OPERATOR_LANES:
        # An action_item only rises to operator tier when authored by the owner
        # (the founder's own word); a bare 3rd-party action item stays primary
        # at most. The other operator lanes are operator-authored by definition.
        if lane == "action_item":
            asserted_by = (item.get("asserted_by") or "").strip().lower()
            owner = (item.get("owner") or "").strip().lower()
            if asserted_by in {"operator", "founder"} or (
                owner and asserted_by == owner
            ):
                return "operator"
            return "primary"
        return "operator"

    # A non-lane operator signal: the operator themselves asserted it.
    if (item.get("asserted_by") or "").strip().lower() in {"operator", "founder"}:
        return "operator"

    if lane in _PRIMARY_LANES:
        return "primary"

    return "secondary"


# --------------------------------------------------------------------------- #
# 3. The resolver                                                             #
# --------------------------------------------------------------------------- #


def _order_key(c: Claim) -> tuple[int, str, str]:
    """Deterministic ordering: tier desc, observed_at desc, claim_id desc.

    Returned so that ``sorted(..., key=_order_key, reverse=True)`` puts the
    winner first. (reverse=True flips all three to descending — and because
    tier is the dominant term, higher tier wins; ties fall to newer
    observed_at, then to the larger claim_id. Fully total, no randomness.)
    """
    return (_tier_rank(c.provenance_tier), c.observed_at, c.claim_id)


def _sorted_group(group: list[Claim]) -> list[Claim]:
    return sorted(group, key=_order_key, reverse=True)


def _best(group: list[Claim]) -> Claim:
    """First of the deterministic ordering (highest tier / newest / largest id)."""
    return _sorted_group(group)[0]


def _arch(c: Claim, reason: str, winner_id: str) -> ArchivedClaim:
    return ArchivedClaim(claim=c, reason=reason, superseded_by=winner_id)


def _replace(c: Claim, **changes: Any) -> Claim:
    """frozen-safe field replacement."""
    return dataclasses.replace(c, **changes)


def resolve_claims(claims: list[Claim]) -> ResolveResult:
    """Resolve same-``fact_key`` conflicts into one current truth per fact.

    Pure + deterministic: same input -> byte-identical output, no ``now()`` /
    randomness. Runs AFTER dedupe, BEFORE detect_conflicts.

    Behavior (BUILD-SPEC-01 §3):
      * Claims with no ``fact_key`` cannot conflict -> passed through untouched.
      * Within a ``fact_key`` group:
          - <= 1 distinct ``fact_value`` (agreement): keep the best
            representative, archive the rest ``reason="duplicate_value"``.
          - >= 2 distinct values, top two cross-tier: the highest-tier newest
            claim supersedes everyone below -> kept ``conflict_status="current"``,
            losers archived ``reason="superseded"``. No dispute.
          - >= 2 distinct values, top two SAME tier: genuine dispute -> winner
            kept ``conflict_status="disputed"`` carrying the same-tier rivals in
            ``competing_claims``; strictly-lower-tier claims archived
            ``reason="superseded_lower_tier"``. Same-tier rivals are NOT
            archived (preserved via competing_claims).
    """
    passthrough = [c for c in claims if not c.fact_key]

    # Group fact_key-bearing claims, preserving first-seen key order for
    # deterministic output ordering of `kept`.
    grouped: dict[str, list[Claim]] = {}
    for c in claims:
        if c.fact_key:
            grouped.setdefault(c.fact_key, []).append(c)

    kept: list[Claim] = list(passthrough)
    archived: list[ArchivedClaim] = []

    for _fact_key, group in grouped.items():
        distinct_values = {c.fact_value for c in group}

        if len(distinct_values) <= 1:
            # Agreement, not a conflict.
            winner = _best(group)
            kept.append(winner)
            archived += [
                _arch(c, "duplicate_value", winner.claim_id)
                for c in group
                if c is not winner
            ]
            continue

        # TRUE conflict (>= 2 distinct values).
        ordered = _sorted_group(group)
        winner = ordered[0]
        runner = ordered[1]

        if _tier_rank(runner.provenance_tier) < _tier_rank(winner.provenance_tier):
            # Cross-tier: winner supersedes everyone below it. No dispute.
            kept.append(_replace(winner, conflict_status="current"))
            archived += [
                _arch(c, "superseded", winner.claim_id) for c in ordered[1:]
            ]
        else:
            # Top two share the highest tier -> genuine dispute.
            top_tier = winner.provenance_tier
            same_tier = [c for c in ordered if c.provenance_tier == top_tier]
            lower_tier = [c for c in ordered if c.provenance_tier != top_tier]
            surfaced = _replace(
                winner,
                conflict_status="disputed",
                competing_claims=tuple(
                    {
                        "statement": c.summary,
                        "source_anchor": c.source_anchor,
                        "fact_value": c.fact_value,
                        "provenance_tier": c.provenance_tier,
                    }
                    for c in same_tier
                    if c.claim_id != winner.claim_id
                ),
            )
            kept.append(surfaced)
            archived += [
                _arch(c, "superseded_lower_tier", winner.claim_id)
                for c in lower_tier
            ]
            # same_tier rivals are NOT archived — preserved via competing_claims.

    return ResolveResult(kept=kept, archived=archived)


# --------------------------------------------------------------------------- #
# Demo (eyeball-able): load sample_claims.json, resolve, print a summary.      #
# --------------------------------------------------------------------------- #


def _claim_from_dict(d: dict[str, Any]) -> Claim:
    """Build a Claim from a plain dict (demo/sample loader). Tuple-typed fields
    are coerced from lists; the tier is derived if absent."""
    data = dict(d)
    if "participants" in data and isinstance(data["participants"], list):
        data["participants"] = tuple(data["participants"])
    if "competing_claims" in data and isinstance(data["competing_claims"], list):
        data["competing_claims"] = tuple(data["competing_claims"])
    if "provenance_tier" not in data:
        data["provenance_tier"] = tier_from_item(data)
    allowed = {f.name for f in dataclasses.fields(Claim)}
    return Claim(**{k: v for k, v in data.items() if k in allowed})


def _run_demo() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    sample_path = os.path.join(here, "sample_claims.json")
    with open(sample_path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    claims = [_claim_from_dict(d) for d in raw]
    result = resolve_claims(claims)

    print(f"input claims : {len(claims)}")
    print(f"kept         : {len(result.kept)}")
    print(f"archived     : {len(result.archived)}")
    print("-" * 60)
    for c in result.kept:
        fk = c.fact_key if c.fact_key else "(no fact_key)"
        fv = c.fact_value if c.fact_value is not None else "-"
        print(
            f"  {fk:>22} -> {str(fv):<10} "
            f"[{c.provenance_tier}, {c.conflict_status}]"
        )
    if result.archived:
        print("-" * 60)
        for a in result.archived:
            print(
                f"  ARCHIVED {a.claim.claim_id} "
                f"({a.reason}, by {a.superseded_by})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_demo())
