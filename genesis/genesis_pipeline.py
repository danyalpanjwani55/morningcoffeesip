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

    asserted_by_raw = event.meta.get("asserted_by")
    item = {
        "source_lane": event.kind,
        "asserted_by": asserted_by_raw,
        "owner": event.meta.get("owner"),
        "provenance_tier": event.meta.get("provenance_tier"),
    }
    tier = tier_from_item(item)

    # asserted_by is PLURAL (list of slugs) in the contract; an event carries at
    # most one asserter in meta -> a 0- or 1-element tuple.
    asserted_by = (asserted_by_raw,) if asserted_by_raw else ()

    # last_evidence_change_at: the event's own time is when this evidence last
    # moved (a fresh single-event projection has no earlier evidence-change).
    return Claim(
        claim_id=event.event_id,
        # PLURAL list of {path, anchor} per the doctrine contract. The corpus
        # anchor's source_id+locator map to the contract's path+anchor; kind is
        # carried for context.
        source_anchors=(
            {
                "path": event.source_id,
                "anchor": event.locator,
                "kind": event.kind,
            },
        ),
        asserted_by=asserted_by,
        observed_at=event.observed_at,
        last_evidence_change_at=event.observed_at,
        confidence="medium",
        recency_status="current",
        conflict_status="aligned",
        summary=text or event.event_id,
        participants=event.participants,
        owner=event.meta.get("owner"),
        fact_key=fact_key,
        fact_value=fact_value,
        provenance_tier=tier,
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


# Markers that bound the generated block. EVERYTHING above the start marker is
# hand-authored prose the writer preserves verbatim (synthesis-writer rule 1).
_GEN_START = "<!-- genesis:generated:start -->"
_GEN_END = "<!-- genesis:generated:end -->"
_MAX_DRAFT_LINES = 200          # synthesis-writer rule 4 (bounded write)
_MAX_ARCHIVE_LINES = 40         # keep the rolling archive bounded under the cap


def _anchor_ref(c: Claim) -> str | None:
    """A short, resolvable source ref from the claim's PLURAL source_anchors,
    or ``None`` if the claim carries no anchor (synthesis-writer rule 2: no
    ground, no write -> the caller drops it from asserted facts)."""
    if not c.source_anchors:
        return None
    first = c.source_anchors[0]
    path = first.get("path") or first.get("source_id") or "?"
    anchor = first.get("anchor") or first.get("locator") or ""
    return f"{path}#{anchor}" if anchor else path


def _fact_line(c: Claim) -> str:
    """One rendered 'Current claims' bullet for a fact-bearing or context claim."""
    fk = c.fact_key or "(context)"
    fv = c.fact_value if c.fact_value is not None else "-"
    return (
        f"- [{c.provenance_tier}/{c.conflict_status}] {fk} = {fv} "
        f"— {c.summary} (src {_anchor_ref(c) or '?'})"
    )


def _parse_prior_fact_values(text: str) -> dict[str, str]:
    """Pull ``{fact_key: fact_value}`` from a prior draft's generated block, so a
    re-run can detect which tracked facts CHANGED value (and must supersede,
    not duplicate). Only the live 'Current claims' bullets are read."""
    block = text.partition(_GEN_START)[2].partition(_GEN_END)[0]
    if not block:
        return {}
    prior: dict[str, str] = {}
    in_current = False
    for raw in block.splitlines():
        line = raw.strip()
        if line.startswith("## Current claims"):
            in_current = True
            continue
        if line.startswith("## "):
            in_current = False
            continue
        if not (in_current and line.startswith("- [")):
            continue
        # "- [tier/status] key = value — summary (src ...)"
        after = line.partition("]")[2].lstrip()
        key, sep, rest = after.partition(" = ")
        if not sep or key == "(context)":
            continue
        value = rest.partition(" — ")[0].strip()
        prior[key.strip()] = value
    return prior


def _prior_archive_lines(text: str) -> list[str]:
    """Existing '## Archived claims' bullets from a prior draft (archive-don't-
    delete: they roll forward, never get dropped, only compacted under the cap)."""
    block = text.partition(_GEN_START)[2].partition(_GEN_END)[0]
    if not block:
        return []
    out: list[str] = []
    in_arch = False
    for raw in block.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("## Archived claims"):
            in_arch = True
            continue
        if stripped.startswith("## "):
            in_arch = False
            continue
        # Skip the empty-state placeholder so it never accumulates.
        if in_arch and stripped.startswith("- ") and stripped != "- (none)":
            out.append(line)
    return out


def _write_pillar_draft(pillar: PillarState, *, today: str = "") -> str:
    """Surgically write/refresh a Type-1 (FOR-AI) pillar draft under OUT_DIR.

    Per ``synthesis-writer/SKILL.md`` semantics (the brain's bounded,
    supersede-don't-duplicate, archive-don't-delete writer), this is NOT a flat
    dump:
      * Hand-authored prose ABOVE the generated marker is preserved verbatim
        (rule 1 — surgical, preserve what you aren't changing).
      * 'Current claims' carries ONE current value per tracked fact; a claim
        with no resolvable anchor is dropped from asserted facts (rule 2 — no
        ground, no write).
      * When a tracked fact's value CHANGED vs the prior draft, the old value is
        moved into '## Archived claims' and a dated '## Evolution' line is added;
        the live section never shows both old and new (rule 3 — supersede,
        don't duplicate; archive, don't delete).
      * The whole draft is capped at ~200 lines; the oldest archived bullets
        compact into a rolling elision (rule 4 — bounded write).

    This remains the ONLY file write the pipeline performs, confined to OUT_DIR
    (asserted by ``_assert_under_out``)."""
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"pillar_{pillar.name}.md")
    _assert_under_out(path)

    prior_text = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            prior_text = fh.read()

    # Preserve any hand-authored header above the generated marker. A first-ever
    # write (or a legacy flat draft with no marker) gets the default header.
    if _GEN_START in prior_text:
        header = prior_text.partition(_GEN_START)[0].rstrip("\n")
    else:
        header = f"# Pillar: {pillar.name}\n\n_status: proposed_"

    prior_values = _parse_prior_fact_values(prior_text)
    archive_lines = _prior_archive_lines(prior_text)

    # Live 'Current claims' — anchored claims only (rule 2). Detect value shifts
    # vs the prior draft and supersede (rule 3).
    current_lines: list[str] = []
    evolution_new: list[str] = []
    seen_keys: set[str] = set()
    for c in pillar.claims:
        if _anchor_ref(c) is None:
            continue  # no ground, no write
        current_lines.append(_fact_line(c))
        if c.fact_key:
            seen_keys.add(c.fact_key)
            old = prior_values.get(c.fact_key)
            new = c.fact_value if c.fact_value is not None else "-"
            if old is not None and old != new:
                # Supersede: archive the old value, never leave both standing.
                archive_lines.append(
                    f"- {c.fact_key} = {old} (superseded by {new}"
                    + (f" on {today}" if today else "")
                    + f"; src {_anchor_ref(c)})"
                )
                evolution_new.append(
                    f"- {today + ' — ' if today else ''}{c.fact_key}: "
                    f"{old} -> {new} (src {_anchor_ref(c)})"
                )

    # Compact the rolling archive under the cap (rule 4) — keep newest, elide
    # the oldest into one summary line so nothing is silently deleted.
    if len(archive_lines) > _MAX_ARCHIVE_LINES:
        elided = len(archive_lines) - _MAX_ARCHIVE_LINES
        archive_lines = (
            [f"- (+{elided} older archived claim(s) elided to stay under the line cap)"]
            + archive_lines[-_MAX_ARCHIVE_LINES:]
        )

    gen: list[str] = [
        _GEN_START,
        "",
        "## Current state summary",
        "",
        pillar.summary,
        "",
        "## Current claims",
        "",
    ]
    gen += current_lines or ["- (no anchored claims yet)"]
    gen += ["", "## Evolution", ""]
    gen += evolution_new or ["- (no superseded facts this pass)"]
    gen += ["", "## Archived claims", ""]
    gen += archive_lines or ["- (none)"]
    gen += ["", _GEN_END]

    body = header + "\n\n" + "\n".join(gen) + "\n"

    # Hard cap (rule 4): if still over, trim from the archive tail (already the
    # oldest-elided form) rather than dropping live current claims.
    out_lines = body.splitlines()
    if len(out_lines) > _MAX_DRAFT_LINES:
        # Drop archive bullets from the end until under the cap, leaving the
        # elision summary in place.
        while len(out_lines) > _MAX_DRAFT_LINES and out_lines and out_lines[-2].startswith("- "):
            del out_lines[-2]
        body = "\n".join(out_lines) + "\n"

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
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
    today: str = "",
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
        today: optional ISO date stamped onto supersession Evolution/Archive
            lines when a draft refresh changes a tracked fact. Empty (default)
            keeps draft writes deterministic for tests.

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
            state.draft_path = _write_pillar_draft(state, today=today)
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
    #
    # The pipeline STOPS here, at the operator gate — it never stands an agent
    # up. When the operator RATIFIES an 'agent' roster proposal in the
    # review/ratify path, that path calls
    # ``agent_wiki_builder.build_wiki_for_ratified_proposal(proposal, corpus,
    # llm, egress)`` to build the agent's DRAFT cited wiki (index + one cited
    # source page per source doc + a concept page + log, under
    # ``genesis/out/wiki/<agent>/``). That seam refuses any non-ratified
    # proposal by default, so proposals-only discipline holds: nothing here
    # auto-builds a wiki.
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
