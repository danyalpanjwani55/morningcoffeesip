"""Meta-initiative deriver (BUILD-SPEC-02 deliverable 2).

For each populated pillar, propose 1-3 meta-initiatives (24-month thrusts).
LLM-driven, but the VERIFY-BEFORE-RELAY rail (SDL-19) is code: every proposal
MUST cite >=1 anchor INTO the corpus/pillar, or it is dropped. A proposal with
zero (or non-resolvable) anchors never reaches the operator.

The LLM contract: given a pillar summary + an evidence list of available
anchors, return a JSON array of objects::

    [{"title": str, "rationale": str, "confidence": "high"|"medium"|"low",
      "anchor_ids": [int, ...]}]   # anchor_ids index INTO the provided evidence

We resolve ``anchor_ids`` back to real ``Anchor`` objects here; any id that
doesn't resolve is ignored, and a proposal left with no valid anchor is
dropped. The LLM never gets to mint an anchor out of thin air.
"""

from __future__ import annotations

import json
from typing import Any

from genesis_contracts import (
    Anchor,
    EgressGate,
    LLM,
    PillarState,
    Proposal,
    new_proposal,
)

MAX_MI_PER_PILLAR = 3

_SYSTEM = (
    "You are a strategy analyst. From a company pillar's populated facts, "
    "propose at most 3 meta-initiatives: 24-month thrusts grounded ONLY in the "
    "provided evidence. Every proposal must cite the evidence ids that support "
    "it. Do not invent facts. Return a JSON array; no prose."
)


def _evidence_block(pillar: PillarState) -> tuple[str, list[Anchor]]:
    """Render the pillar's available anchors as a numbered evidence list the
    LLM can cite by index, and return the index->Anchor table."""
    anchors = list(pillar.anchors)
    lines = []
    for i, a in enumerate(anchors):
        lines.append(f"[{i}] {a.kind}:{a.source_id}:{a.locator}")
    return "\n".join(lines), anchors


def _build_user_prompt(pillar: PillarState, evidence_text: str) -> str:
    return (
        f"PILLAR: {pillar.name}\n"
        f"SUMMARY: {pillar.summary}\n\n"
        f"EVIDENCE (cite by id):\n{evidence_text}\n\n"
        "Return JSON: "
        '[{"title": ..., "rationale": ..., "confidence": ..., "anchor_ids": [...]}]'
    )


def _parse_llm_json(raw: str) -> list[dict[str, Any]]:
    """Tolerantly parse the model's JSON array. Returns [] on any malformed
    output (fail closed -> no proposals rather than garbage)."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]


def _resolve_anchors(
    anchor_ids: Any, evidence: list[Anchor]
) -> tuple[Anchor, ...]:
    """Map LLM-returned indices back to real Anchors. Out-of-range / non-int
    ids are silently dropped (the model can't fabricate an anchor)."""
    if not isinstance(anchor_ids, list):
        return ()
    resolved: list[Anchor] = []
    seen: set[int] = set()
    for raw_id in anchor_ids:
        if isinstance(raw_id, bool):       # bool is an int subclass; reject
            continue
        if not isinstance(raw_id, int):
            continue
        if raw_id in seen or not (0 <= raw_id < len(evidence)):
            continue
        seen.add(raw_id)
        resolved.append(evidence[raw_id])
    return tuple(resolved)


def derive_meta_initiatives(
    pillars: dict[str, PillarState],
    *,
    llm: LLM,
    egress: EgressGate,
) -> list[Proposal]:
    """Propose meta-initiatives per populated pillar. Anchor-or-drop enforced.

    Pillars with no anchors are skipped entirely (nothing to ground a thrust
    in). Each surviving proposal carries >=1 real corpus/pillar anchor.
    """
    proposals: list[Proposal] = []

    for name in sorted(pillars):           # deterministic pillar order
        pillar = pillars[name]
        if not pillar.anchors:
            continue                        # nothing to ground proposals in

        evidence_text, evidence = _evidence_block(pillar)
        user = _build_user_prompt(pillar, evidence_text)
        # Data-boundary rail: the prompt may carry pillar prose -> guard it.
        safe_user = egress.guard(user)
        raw = llm.complete(_SYSTEM, safe_user, max_tokens=1024)

        for item in _parse_llm_json(raw)[:MAX_MI_PER_PILLAR]:
            anchors = _resolve_anchors(item.get("anchor_ids"), evidence)
            if not anchors:
                # VERIFY-BEFORE-RELAY: zero valid anchors -> DROP.
                continue
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            proposals.append(
                new_proposal(
                    type="meta_initiative",
                    confidence=str(item.get("confidence", "low")),
                    rationale=str(item.get("rationale", "")).strip(),
                    source_anchors=anchors,
                    payload={"pillar": name, "title": title},
                )
            )

    return proposals
