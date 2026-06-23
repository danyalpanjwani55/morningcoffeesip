"""Tests for the L2 surgical pillar-draft writer (synthesis-writer semantics).

The flat ``_write_pillar_draft`` dump was replaced with a bounded,
supersede-don't-duplicate, archive-don't-delete, header-preserving writer per
``synthesis-writer/SKILL.md``. These tests pin that behavior:

  * surgical: hand-authored prose ABOVE the generated marker survives a re-run;
  * supersede-don't-duplicate: a changed fact value never leaves BOTH old and
    new standing in 'Current claims' — the old moves to '## Archived claims'
    with a dated '## Evolution' line;
  * archive-don't-delete: prior archive entries roll forward;
  * no-ground-no-write: a claim with empty source_anchors is dropped from the
    asserted facts;
  * bounded: the draft stays under the ~200-line cap.

Run: ``pytest -q`` in this directory.
"""

from __future__ import annotations

import os

import genesis_pipeline as gp
from genesis_contracts import PillarState
from genesis_resolver import Claim


def _claim(cid: str, fk: str | None, fv: str | None, *,
           tier: str = "operator", anchors=None) -> Claim:
    return Claim(
        claim_id=cid,
        source_anchors=(({"path": cid, "anchor": "L1"},) if anchors is None
                        else anchors),
        asserted_by=(),
        observed_at="2026-06-20T12:00:00Z",
        last_evidence_change_at="2026-06-20T12:00:00Z",
        confidence="high",
        recency_status="current",
        conflict_status="aligned",
        summary=f"{fk or 'note'} = {fv}",
        fact_key=fk,
        fact_value=fv,
        provenance_tier=tier,
    )


def _write(tmp_path, monkeypatch, pillar: PillarState, *, today: str = "") -> str:
    monkeypatch.setattr(gp, "OUT_DIR", str(tmp_path))
    return gp._write_pillar_draft(pillar, today=today)


# --------------------------------------------------------------------------- #
# supersede-don't-duplicate                                                    #
# --------------------------------------------------------------------------- #


def test_changed_fact_supersedes_and_does_not_duplicate(tmp_path, monkeypatch):
    p1 = PillarState(name="gtm", summary="v1",
                     claims=[_claim("e1", "launch_date", "OCT")])
    path = _write(tmp_path, monkeypatch, p1, today="2026-06-20")

    p2 = PillarState(name="gtm", summary="v2",
                     claims=[_claim("e2", "launch_date", "NOV")])
    _write(tmp_path, monkeypatch, p2, today="2026-06-21")

    text = open(path, encoding="utf-8").read()
    current = text.partition("## Current claims")[2].partition("## Evolution")[0]
    archived = text.partition("## Archived claims")[2]

    # Current shows ONLY the new value — never both old and new.
    assert "NOV" in current
    assert "OCT" not in current
    # The old value is archived (archive-don't-delete) with a dated note.
    assert "launch_date = OCT" in archived
    assert "2026-06-21" in archived
    # An Evolution line records the shift.
    assert "OCT -> NOV" in text


def test_unchanged_fact_does_not_create_an_archive_entry(tmp_path, monkeypatch):
    p1 = PillarState(name="gtm", summary="v1",
                     claims=[_claim("e1", "launch_date", "OCT")])
    path = _write(tmp_path, monkeypatch, p1, today="2026-06-20")
    p2 = PillarState(name="gtm", summary="v1-again",
                     claims=[_claim("e1", "launch_date", "OCT")])
    _write(tmp_path, monkeypatch, p2, today="2026-06-21")

    archived = open(path, encoding="utf-8").read().partition("## Archived claims")[2]
    assert "launch_date" not in archived  # nothing changed -> nothing archived


# --------------------------------------------------------------------------- #
# surgical: preserve hand-authored header                                      #
# --------------------------------------------------------------------------- #


def test_hand_authored_header_is_preserved_across_reruns(tmp_path, monkeypatch):
    p1 = PillarState(name="gtm", summary="v1",
                     claims=[_claim("e1", "k", "A")])
    path = _write(tmp_path, monkeypatch, p1)

    # Inject a hand-authored paragraph ABOVE the generated marker.
    original = open(path, encoding="utf-8").read()
    edited = original.replace(
        gp._GEN_START,
        "## Operator note\n\nThis line is hand-authored and must survive.\n\n"
        + gp._GEN_START,
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(edited)

    # Re-run with a different fact; the hand-authored line must still be there.
    p2 = PillarState(name="gtm", summary="v2",
                     claims=[_claim("e2", "k", "B")])
    _write(tmp_path, monkeypatch, p2, today="2026-06-21")

    text = open(path, encoding="utf-8").read()
    assert "This line is hand-authored and must survive." in text
    # and it stays ABOVE the generated block
    assert text.index("hand-authored") < text.index(gp._GEN_START)


# --------------------------------------------------------------------------- #
# no-ground-no-write                                                           #
# --------------------------------------------------------------------------- #


def test_claim_without_anchor_is_dropped_from_current(tmp_path, monkeypatch):
    grounded = _claim("g", "k1", "A")
    ungrounded = _claim("u", "k2", "B", anchors=())  # no source_anchors
    p = PillarState(name="gtm", summary="s", claims=[grounded, ungrounded])
    path = _write(tmp_path, monkeypatch, p)

    current = open(path, encoding="utf-8").read().partition(
        "## Current claims")[2].partition("## Evolution")[0]
    assert "k1 = A" in current        # grounded survives
    assert "k2 = B" not in current    # ungrounded dropped (no ground, no write)


# --------------------------------------------------------------------------- #
# bounded write (<= ~200 lines)                                                #
# --------------------------------------------------------------------------- #


def test_draft_stays_under_the_line_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(gp, "OUT_DIR", str(tmp_path))
    path = os.path.join(str(tmp_path), "pillar_gtm.md")

    # Churn one fact's value many times to pile up the archive, then assert the
    # bounded write keeps the file under the cap with an elision summary.
    prev_today = None
    for i in range(120):
        p = PillarState(
            name="gtm", summary=f"summary {i}",
            claims=[_claim(f"e{i}", "launch_date", f"V{i}")],
        )
        gp._write_pillar_draft(p, today=f"2026-07-{(i % 28) + 1:02d}")
        prev_today = i

    text = open(path, encoding="utf-8").read()
    assert len(text.splitlines()) <= gp._MAX_DRAFT_LINES
    # nothing silently lost: the elision summary accounts for the dropped ones
    assert "older archived claim(s) elided" in text
    # the latest value is still the only current one
    current = text.partition("## Current claims")[2].partition("## Evolution")[0]
    assert f"V{prev_today}" in current


# --------------------------------------------------------------------------- #
# legacy flat draft (no marker) is migrated, not preserved as header           #
# --------------------------------------------------------------------------- #


def test_legacy_flat_draft_body_is_not_carried_as_header(tmp_path, monkeypatch):
    monkeypatch.setattr(gp, "OUT_DIR", str(tmp_path))
    path = os.path.join(str(tmp_path), "pillar_gtm.md")
    # Simulate a legacy flat dump (the OLD format, no generated marker).
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Pillar: gtm\n\n_status: proposed_\n\n"
                 "## Resolved claims\n\n- [primary/none] stale = OLD\n")

    p = PillarState(name="gtm", summary="fresh", claims=[_claim("e1", "k", "A")])
    gp._write_pillar_draft(p, today="2026-06-21")

    text = open(path, encoding="utf-8").read()
    assert gp._GEN_START in text                 # now has a generated block
    assert "## Resolved claims" not in text      # legacy body NOT preserved
    assert "stale = OLD" not in text
