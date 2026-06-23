"""Genesis pipeline orchestrator (BUILD-SPEC-02 deliverable 1).

Runs the full genesis pass:

    ingest (full-corpus) -> claims -> resolve_claims (BUILD-SPEC-01)
      -> write pillar drafts -> derive meta-initiatives -> propose roster
      -> assemble ReviewPacket

Pure-Python control flow; ALL model judgment goes through the injected ``llm``
(swappable + stub-testable). Writes nothing outside ``genesis/out/`` and emits
only ``status="proposed"`` artifacts. The data-boundary rail (``EgressGate``)
guards every foreign-model prompt; verify-before-relay drops un-anchored
proposals; the operator gate is the returned ``ReviewPacket`` (applies nothing).

Full-corpus mode: ``since="inception"`` walks the entire corpus in one pass
(``corpus.events_since`` yields all events with no lower bound); a date yields
only strictly-newer events.
"""

from __future__ import annotations

import json
import os
import re
from typing import Iterable

from genesis_contracts import (
    Anchor,
    Corpus,
    EgressGate,
    Event,
    LLM,
    PillarState,
    Proposal,
    ReviewPacket,
)
from genesis_resolver import Claim, resolve_claims, tier_from_item
from meta_initiative_deriver import derive_meta_initiatives
from review_surface import build_review_packet
from roster_proposer import propose_roster

# Output is confined to this directory (created on demand).
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

# A minimal default pillar topic map for routing events -> pillars. Generic /
# domain-agnostic; a deployment can pass its own via ``pillar_keywords``.
_DEFAULT_PILLAR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "product": ("product", "feature", "roadmap", "launch", "ship", "design"),
    "gtm": ("price", "pricing", "sales", "market", "customer", "gtm", "deal"),
    "people": ("hire", "hiring", "team", "agent", "role", "recruit", "candidate"),
    "operations": ("ops", "operation", "process", "vendor", "logistics", "finance"),
}
_FALLBACK_PILLAR = "general"


def _route_pillar(event: Event, pillar_keywords: dict[str, tuple[str, ...]]) -> str:
    """Deterministic keyword routing. First pillar (sorted) whose keyword
    appears in the event text/kind wins; else the fallback pillar.

    (BUILD-SPEC-02 notes semantic LLM routing is a LATER slice — this slice
    uses a pure, testable keyword router so the pipeline is deterministic.)"""
    haystack = f"{event.kind} {event.text}".lower()
    for pillar in sorted(pillar_keywords):
        for kw in pillar_keywords[pillar]:
            if kw in haystack:
                return pillar
    return _FALLBACK_PILLAR


_FACT_RE = re.compile(r"(?P<key>[a-z0-9_]+)\s*=\s*(?P<value>.+)$", re.IGNORECASE)


def _claim_from_event(event: Event, category: str) -> Claim:
    """Project one corpus event into a resolver ``Claim``.

    A ``key = value`` line in the event text is parsed into ``fact_key`` /
    ``fact_value`` so conflicting facts collide in the resolver; otherwise the
    claim is a non-conflicting context claim (no fact_key). The provenance tier
    is derived via ``tier_from_item`` from the event's lane + meta."""
    text = event.text.strip()
    fact_key = None
    fact_value = None
    m = _FACT_RE.search(text.splitlines()[0] if text else "")
    if m:
        fact_key = m.group("key").strip().lower()
        fact_value = m.group("value").strip()

    item = {
        "source_lane": event.kind,
        "asserted_by": event.meta.get("asserted_by"),
        "owner": event.meta.get("owner"),
        "provenance_tier": event.meta.get("provenance_tier"),
    }
    tier = tier_from_item(item)

    return Claim(
        claim_id=event.event_id,
        category=category,
        summary=text or event.event_id,
        observed_at=event.observed_at,
        source_lane=event.kind,
        source_anchor={
            "source_id": event.source_id,
            "kind": event.kind,
            "locator": event.locator,
        },
        confidence="medium",
        recency="current",
        conflict_status="none",
        participants=event.participants,
        owner=event.meta.get("owner"),
        fact_key=fact_key,
        fact_value=fact_value,
        provenance_tier=tier,
        asserted_by=event.meta.get("asserted_by"),
    )


def _summarize_pillar(name: str, claims: list[Claim]) -> str:
    """A short, plain-English pillar summary (deterministic; no model needed —
    the operator-facing taste lives in the review surface, this is structural)."""
    n_facts = len({c.fact_key for c in claims if c.fact_key})
    disputed = sum(1 for c in claims if c.conflict_status == "disputed")
    bits = [f"{len(claims)} note{'s' if len(claims) != 1 else ''}"]
    if n_facts:
        bits.append(f"{n_facts} tracked fact{'s' if n_facts != 1 else ''}")
    if disputed:
        bits.append(f"{disputed} needing your call")
    return f"{name.title()} pillar: " + ", ".join(bits) + "."


def _write_pillar_draft(pillar: PillarState) -> str:
    """Write a Type-1 (FOR-AI) pillar draft under OUT_DIR. Returns the path.

    This is the ONLY file write the pipeline performs, and it is confined to
    OUT_DIR (asserted by ``_assert_under_out``)."""
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"pillar_{pillar.name}.md")
    _assert_under_out(path)
    lines = [f"# Pillar: {pillar.name}", "", f"_status: proposed_", "",
             pillar.summary, "", "## Resolved claims", ""]
    for c in pillar.claims:
        fk = c.fact_key or "(context)"
        fv = c.fact_value if c.fact_value is not None else "-"
        lines.append(
            f"- [{c.provenance_tier}/{c.conflict_status}] {fk} = {fv} "
            f"— {c.summary} (src {c.source_anchor.get('source_id', '?')})"
        )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _assert_under_out(path: str) -> None:
    """Guard: refuse any write whose resolved path escapes OUT_DIR."""
    real_out = os.path.realpath(OUT_DIR)
    real_path = os.path.realpath(path)
    if not (real_path == real_out or real_path.startswith(real_out + os.sep)):
        raise RuntimeError(f"Refusing write outside OUT_DIR: {path}")


def run_genesis(
    corpus: Corpus,
    *,
    roster: list[str],
    since: str = "inception",
    llm: LLM,
    egress: EgressGate,
    pillar_keywords: dict[str, tuple[str, ...]] | None = None,
    write_drafts: bool = True,
) -> ReviewPacket:
    """Run the full genesis pass and return an operator ``ReviewPacket``.

    Args:
        corpus: the ingested corpus (``events_since`` drives full-corpus mode).
        roster: the base roster of agents (never re-proposed).
        since: ``"inception"`` for a full-corpus pass, else an ISO date.
        llm: injected model (all judgment routes through it).
        egress: data-boundary gate (guards every foreign-model prompt).
        pillar_keywords: optional custom pillar routing map.
        write_drafts: write pillar drafts to OUT_DIR (off in tests that assert
            no writes).

    Returns:
        A ``ReviewPacket`` whose every proposal is ``status="proposed"`` with
        >=1 anchor, and whose ``summary_md`` opens "In plain terms".
    """
    keywords = pillar_keywords or _DEFAULT_PILLAR_KEYWORDS

    # 1. Ingest (full-corpus) + project events into per-pillar claims.
    pillar_claims: dict[str, list[Claim]] = {}
    pillar_anchors: dict[str, list[Anchor]] = {}
    for event in corpus.events_since(since):
        pillar = _route_pillar(event, keywords)
        claim = _claim_from_event(event, category=pillar)
        pillar_claims.setdefault(pillar, []).append(claim)
        pillar_anchors.setdefault(pillar, []).append(event.anchor())

    # 2. Resolve conflicts per pillar (BUILD-SPEC-01) + build PillarState.
    pillars: dict[str, PillarState] = {}
    for name in sorted(pillar_claims):
        resolved = resolve_claims(pillar_claims[name])
        state = PillarState(
            name=name,
            summary=_summarize_pillar(name, resolved.kept),
            claims=resolved.kept,
            anchors=pillar_anchors.get(name, []),
        )
        if write_drafts:
            state.draft_path = _write_pillar_draft(state)
        pillars[name] = state

    # 3. Derive meta-initiatives (anchor-or-drop) + propose roster (MIN_EVIDENCE).
    mi_proposals = derive_meta_initiatives(pillars, llm=llm, egress=egress)

    people_pillar = pillars.get("people", PillarState(name="people"))
    roster_proposals = propose_roster(
        corpus, people_pillar, roster, llm=llm, egress=egress
    )

    # 4. (doc-reorg proposals are a later slice — none derived here.)
    doc_reorg_proposals: list[Proposal] = []

    # 5. Assemble the operator review packet (Type-2; applies nothing).
    packet = build_review_packet(
        pillars, mi_proposals, roster_proposals, doc_reorg_proposals
    )

    # Final rail check: nothing in the packet is anything but 'proposed', and
    # every proposal is anchored (defense in depth over the per-stage drops).
    for p in packet.proposals:
        assert p.status == "proposed", f"non-proposed artifact escaped: {p.id}"
        assert p.is_anchored(), f"un-anchored proposal escaped: {p.id}"

    return packet


# --------------------------------------------------------------------------- #
# Demo: a tiny in-memory corpus + a canned LLM, printed to stdout.            #
# --------------------------------------------------------------------------- #


def _demo() -> int:
    from genesis_contracts import InMemoryCorpus

    class _DemoLLM:
        """A canned LLM for the demo (deterministic)."""

        def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
            if "PILLAR:" in user:
                # one MI citing evidence id 0
                return json.dumps(
                    [{"title": "Win the beachhead segment",
                      "rationale": "Repeated pricing + launch decisions point to a focused GTM push.",
                      "confidence": "medium", "anchor_ids": [0]}]
                )
            if "CORPUS EVIDENCE" in user:
                # propose a 'gtm' agent citing 3 distinct ids
                return json.dumps(
                    [{"slug": "gtm-lead", "domain": "go-to-market",
                      "rationale": "Pricing and customer threads recur enough to warrant a dedicated lead.",
                      "anchor_ids": [0, 1, 2]}]
                )
            return "[]"

    events: Iterable[Event] = [
        Event("e1", "2026-06-18T09:00:00Z", "decision",
              "launch_date = 2026-10-15", "standup", "L1",
              meta={"asserted_by": "operator"}),
        Event("e2", "2026-06-10T12:00:00Z", "web",
              "launch_date = 2026-09-01", "partner-blog", "p3"),
        Event("e3", "2026-06-19T08:00:00Z", "email",
              "list_price = 4900", "pricing-thread", "msg7"),
        Event("e4", "2026-06-21T08:00:00Z", "meeting",
              "list_price = 5200", "pricing-review", "L8"),
        Event("e5", "2026-06-20T10:00:00Z", "email",
              "customer onboarding plan for the first deal", "cust-thread", "m2"),
    ]
    corpus = InMemoryCorpus(events)
    packet = run_genesis(
        corpus,
        roster=["product", "ops"],
        since="inception",
        llm=_DemoLLM(),
        egress=EgressGate(),
        write_drafts=True,
    )
    print(packet.summary_md)
    print("-" * 60)
    print(f"proposals: {len(packet.proposals)} "
          f"(all proposed={all(p.status == 'proposed' for p in packet.proposals)}, "
          f"all anchored={all(p.is_anchored() for p in packet.proposals)})")
    print(f"drafts written under: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
