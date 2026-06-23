"""Roster proposer (BUILD-SPEC-02 deliverable 3).

From the corpus + people pillar, propose which sub-agents to create BEYOND the
``base_roster``. Rules (enforced as code):
  * Propose an agent for a domain only when >= ``MIN_EVIDENCE`` (default 3)
    DISTINCT anchored signals support it — never invent a role from thin air.
  * Base-roster agents are never re-proposed (case-insensitive slug match).
  * Every proposal carries its supporting anchors (verify-before-relay).

The LLM's job is to read the corpus and *cluster* recurring work into candidate
domains; it returns a JSON array. We then re-validate every candidate against
the real evidence count and the base roster — the model cannot bypass the
MIN_EVIDENCE floor or smuggle in a base-roster slug.

LLM contract — returns::

    [{"slug": str, "domain": str, "rationale": str, "anchor_ids": [int, ...]}]
      # anchor_ids index INTO the provided corpus-evidence list
"""

from __future__ import annotations

import json
import re
from typing import Any

from genesis_contracts import (
    Anchor,
    Corpus,
    EgressGate,
    Event,
    LLM,
    PillarState,
    Proposal,
    new_proposal,
)

MIN_EVIDENCE = 3

_SYSTEM = (
    "You are an org designer. From a company's corpus, cluster the recurring "
    "work into candidate specialist sub-agents. Propose a sub-agent ONLY when "
    "several distinct pieces of evidence justify a dedicated role. Cite the "
    "evidence ids for each. Do not propose roles already in the base roster. "
    "Return a JSON array; no prose."
)


def _normalize_slug(slug: str) -> str:
    """Lower-case, collapse non-alphanumerics to hyphens (stable comparison)."""
    return re.sub(r"[^a-z0-9]+", "-", slug.strip().lower()).strip("-")


def _corpus_evidence(corpus: Corpus, people: PillarState) -> tuple[str, list[Anchor], list[Event]]:
    """Build the numbered evidence list from corpus events (+ the people
    pillar's own anchors), returning the rendered text + index table."""
    events = list(corpus.events_since("inception"))
    anchors: list[Anchor] = [e.anchor() for e in events]
    # include the people pillar's anchors too (org signal lives there)
    anchors += list(people.anchors)

    lines = []
    for i, e in enumerate(events):
        snippet = e.text.strip().replace("\n", " ")
        if len(snippet) > 80:
            snippet = snippet[:77] + "..."
        lines.append(f"[{i}] {e.kind}:{e.source_id}:{e.locator} — {snippet}")
    return "\n".join(lines), anchors, events


def _build_user_prompt(evidence_text: str, base_roster: list[str]) -> str:
    return (
        f"BASE ROSTER (do not re-propose): {', '.join(base_roster) or '(none)'}\n\n"
        f"CORPUS EVIDENCE (cite by id):\n{evidence_text}\n\n"
        "Return JSON: "
        '[{"slug": ..., "domain": ..., "rationale": ..., "anchor_ids": [...]}]'
    )


def _parse_llm_json(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]


def _resolve_anchors(anchor_ids: Any, evidence: list[Anchor]) -> tuple[Anchor, ...]:
    """Map LLM indices to real Anchors, de-duplicated, in-range only."""
    if not isinstance(anchor_ids, list):
        return ()
    resolved: list[Anchor] = []
    seen: set[int] = set()
    for raw_id in anchor_ids:
        if isinstance(raw_id, bool) or not isinstance(raw_id, int):
            continue
        if raw_id in seen or not (0 <= raw_id < len(evidence)):
            continue
        seen.add(raw_id)
        resolved.append(evidence[raw_id])
    return tuple(resolved)


def _distinct_signal_count(anchors: tuple[Anchor, ...]) -> int:
    """Count DISTINCT anchored signals (by source_id + locator). Two citations
    to the same exact locator are one signal — prevents a single source being
    inflated into 'recurring' evidence."""
    return len({(a.source_id, a.locator) for a in anchors})


def propose_roster(
    corpus: Corpus,
    people: PillarState,
    base_roster: list[str],
    *,
    llm: LLM,
    egress: EgressGate,
) -> list[Proposal]:
    """Propose sub-agents beyond the base roster, gated on MIN_EVIDENCE distinct
    anchored signals. Base-roster slugs are never re-proposed."""
    base_norm = {_normalize_slug(s) for s in base_roster}

    evidence_text, evidence, _events = _corpus_evidence(corpus, people)
    user = _build_user_prompt(evidence_text, base_roster)
    safe_user = egress.guard(user)         # data-boundary rail
    raw = llm.complete(_SYSTEM, safe_user, max_tokens=1024)

    proposals: list[Proposal] = []
    emitted: set[str] = set()

    for item in _parse_llm_json(raw):
        slug = _normalize_slug(str(item.get("slug", "")))
        if not slug:
            continue
        if slug in base_norm:
            continue                        # never re-propose a base-roster agent
        if slug in emitted:
            continue                        # de-dupe within one pass

        anchors = _resolve_anchors(item.get("anchor_ids"), evidence)
        if _distinct_signal_count(anchors) < MIN_EVIDENCE:
            # Not enough distinct evidence -> do NOT invent the role.
            continue

        emitted.add(slug)
        proposals.append(
            new_proposal(
                type="agent",
                confidence=str(item.get("confidence", "medium")),
                rationale=str(item.get("rationale", "")).strip(),
                source_anchors=anchors,
                payload={
                    "slug": slug,
                    "domain": str(item.get("domain", "")).strip(),
                    "suggested_wiki_sources": [
                        {"source_id": a.source_id, "kind": a.kind, "locator": a.locator}
                        for a in anchors
                    ],
                },
            )
        )

    # deterministic order by slug
    proposals.sort(key=lambda p: p.payload.get("slug", ""))
    return proposals
