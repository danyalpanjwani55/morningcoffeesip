"""Review surface assembler (BUILD-SPEC-02 deliverable 4).

Assembles the operator review packet — Type-2 (FOR-THE-OPERATOR): opens
"In plain terms," zero unexplained jargon, and NO raw ``source_id`` codes in
the operator prose (evidence lives in a separate, clearly-labelled block).

The packet asserts nothing as fact and applies nothing: every item is
``status="proposed"`` awaiting ``ratify | edit | reject``.

Sections:
  (a) "Here's what I understood"   — per-pillar plain summary + anchor count
  (b) "Agents I propose"           — each with the one-line why
  (c) "Meta-initiatives I derived" — each with the one-line why
  (d) "Docs I'd reorganize"        — each with the one-line why
"""

from __future__ import annotations

from genesis_contracts import (
    PillarState,
    Proposal,
    ReviewPacket,
)

PLAIN_TERMS_HEADER = "In plain terms"


def _evidence_phrase(p: Proposal) -> str:
    """Operator-safe evidence phrasing: a COUNT, never raw source_id codes.

    The codes themselves live in the separate evidence block, not the prose."""
    n = len(p.source_anchors)
    return f"{n} supporting reference" + ("s" if n != 1 else "")


def _section_a(pillars: dict[str, PillarState]) -> list[str]:
    out = ["## Here's what I understood", ""]
    if not pillars:
        out.append("_(No pillars were populated.)_")
        out.append("")
        return out
    for name in sorted(pillars):
        pillar = pillars[name]
        summary = pillar.summary.strip() or "(no summary yet)"
        out.append(
            f"- **{name}** — {summary} "
            f"_(based on {pillar.anchor_count} reference"
            f"{'s' if pillar.anchor_count != 1 else ''})_"
        )
    out.append("")
    return out


def _proposal_section(title: str, proposals: list[Proposal], label_key: str) -> list[str]:
    out = [f"## {title}", ""]
    if not proposals:
        out.append("_(None proposed.)_")
        out.append("")
        return out
    for p in proposals:
        label = str(p.payload.get(label_key, "")).strip() or p.id
        why = p.rationale.strip() or "(no rationale given)"
        out.append(
            f"- **{label}** — {why} "
            f"[{p.confidence} confidence · {_evidence_phrase(p)} · "
            f"status: {p.status}]"
        )
    out.append("")
    return out


def _evidence_block(proposals: list[Proposal]) -> list[str]:
    """The SEPARATE evidence block — this is where raw source_id codes are
    allowed to appear (clearly fenced off from the operator prose)."""
    out = ["## Evidence (references behind each proposal)", ""]
    if not proposals:
        out.append("_(No proposals.)_")
        out.append("")
        return out
    for p in proposals:
        label = (
            str(p.payload.get("title")
                or p.payload.get("slug")
                or p.payload.get("label")
                or "").strip()
            or p.id
        )
        out.append(f"- **{label}** (`{p.id}`):")
        for a in p.source_anchors:
            out.append(f"    - `{a.source_id}` · {a.kind} · `{a.locator}`")
    out.append("")
    return out


def build_review_packet(
    pillars: dict[str, PillarState],
    mi_proposals: list[Proposal],
    roster_proposals: list[Proposal],
    doc_reorg_proposals: list[Proposal],
) -> ReviewPacket:
    """Assemble the Type-2 operator review packet. Asserts/auto-applies nothing.

    All proposals are forced to ``status="proposed"`` defensively (the packet is
    a gate surface, never an apply surface)."""
    all_proposals: list[Proposal] = [
        *roster_proposals,
        *mi_proposals,
        *doc_reorg_proposals,
    ]
    # Defensive: the review surface NEVER carries an applied proposal.
    for p in all_proposals:
        if p.status != "proposed":
            raise ValueError(
                f"ReviewPacket may only carry status='proposed' proposals; "
                f"got {p.id!r} with status={p.status!r}"
            )

    lines: list[str] = []
    lines.append(f"# {PLAIN_TERMS_HEADER}")
    lines.append("")
    lines.append(
        "Below is what I gathered from your records, plus what I'd suggest "
        "next. **Nothing here is decided or applied** — each item is a "
        "suggestion for you to approve, edit, or reject. Every suggestion shows "
        "how many references back it up; the references themselves are listed "
        "at the bottom."
    )
    lines.append("")

    lines += _section_a(pillars)
    lines += _proposal_section("Agents I propose", roster_proposals, "slug")
    lines += _proposal_section("Meta-initiatives I derived", mi_proposals, "title")
    lines += _proposal_section("Docs I'd reorganize", doc_reorg_proposals, "label")
    lines += _evidence_block(all_proposals)

    summary_md = "\n".join(lines).rstrip() + "\n"

    return ReviewPacket(
        pillars=pillars,
        proposals=all_proposals,
        summary_md=summary_md,
    )
