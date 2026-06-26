"""Tests for ITEM 2.1 — lossless auto-split of the pillar-draft cap.

The old hard cap dropped archived bullets outright (``del out_lines[-2]``). It
is replaced by a LOSSLESS auto-split: when a pillar draft would exceed the line
cap, the OLDEST archive bullets are RELOCATED into a dated child page and the
parent re-points to it. The never-delete rule (CLAUDE.md §3 knowledge-hygiene /
docs/SYSTEM.md) must hold even for the superseded archive.

These tests pin:
  * a draft driven over the cap SPLITS — a child page is created and the parent
    re-points to it;
  * a content/line-set check proves NO archive bullet is lost across the split
    (parent ∪ child carries every archived bullet that existed pre-split);
  * the parent ends under the cap;
  * a normal-sized draft does NOT split (no child page, no pointer);
  * chained splits never clobber an earlier child page.

Run: ``/usr/bin/python3 -B -m pytest -q`` in this directory.
"""

from __future__ import annotations

import os

import genesis_pipeline as gp
from genesis_contracts import PillarState
from genesis_resolver import Claim


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


def _claim(cid: str, fk: str | None, fv: str | None) -> Claim:
    return Claim(
        claim_id=cid,
        source_anchors=({"path": cid, "anchor": "L1"},),
        asserted_by=(),
        observed_at="2026-06-20T12:00:00Z",
        last_evidence_change_at="2026-06-20T12:00:00Z",
        confidence="high",
        recency_status="current",
        conflict_status="aligned",
        summary=f"{fk or 'note'} = {fv}",
        fact_key=fk,
        fact_value=fv,
        provenance_tier="operator",
    )


def _archive_bullets(text: str) -> list[str]:
    """Every archived-claim bullet in a draft body (parent or child), stripped of
    the leading '- '. Excludes the empty-state placeholder and any pointer line."""
    out: list[str] = []
    in_arch = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## Archived claims"):
            in_arch = True
            continue
        if line.startswith("## ") or line == gp._GEN_END:
            in_arch = False
            continue
        if not (in_arch and line.startswith("- ")):
            continue
        if line == "- (none)":
            continue
        # the parent's child-page pointer is not an archived fact bullet
        if "moved to [" in line:
            continue
        # the soft-elide count summary ("+N older … elided") is a meta-line, not a fact
        if "elided" in line:
            continue
        out.append(line[2:])
    return out


def _many_live_claims(n: int) -> list[Claim]:
    """N distinct anchored live claims (each renders one 'Current claims' line) —
    the unbounded live section is what can push a single write over the cap."""
    return [_claim(f"live{i}", f"fact_{i}", f"V{i}") for i in range(n)]


# --------------------------------------------------------------------------- #
# Build a prior draft that already carries a full (soft-capped) archive, then   #
# drive the next write over the line cap with many live claims.                 #
# --------------------------------------------------------------------------- #


def _seed_full_archive(tmp_path, monkeypatch) -> str:
    """Churn one fact's value enough to fill the rolling archive to its soft cap,
    returning the draft path. Archive bullets are real superseded-fact text."""
    monkeypatch.setattr(gp, "OUT_DIR", str(tmp_path))
    path = os.path.join(str(tmp_path), "pillar_gtm.md")
    for i in range(gp._MAX_ARCHIVE_LINES + 5):
        p = PillarState(
            name="gtm", summary=f"summary {i}",
            claims=[_claim(f"e{i}", "launch_date", f"VALUE_{i}")],
        )
        gp._write_pillar_draft(p, today=f"2026-07-{(i % 28) + 1:02d}")
    return path


def test_oversize_draft_splits_into_a_child_page(tmp_path, monkeypatch):
    path = _seed_full_archive(tmp_path, monkeypatch)

    pre = open(path, encoding="utf-8").read()
    pre_archive = set(_archive_bullets(pre))
    assert pre_archive, "fixture should leave a populated archive"
    assert len(pre.splitlines()) <= gp._MAX_DRAFT_LINES  # not yet split

    # Now write a draft whose LIVE section alone blows past the cap. The archive
    # rolls forward; the parent must relocate (not drop) the overflow.
    big = PillarState(
        name="gtm", summary="big write",
        claims=_many_live_claims(gp._MAX_DRAFT_LINES + 50),
    )
    gp._write_pillar_draft(big, today="2026-08-01")

    child = gp._archive_child_path(path, 1)
    assert os.path.isfile(child), "an over-cap draft must split off a child page"

    parent = open(path, encoding="utf-8").read()
    child_text = open(child, encoding="utf-8").read()

    # 1) the parent re-points to the child (operator can follow the trail)
    assert os.path.basename(child) in parent
    assert "moved to" in parent

    # 2) the parent is back under the cap
    assert len(parent.splitlines()) <= gp._MAX_DRAFT_LINES

    # 3) LOSSLESS: every archived bullet that existed pre-split still exists,
    #    now across parent ∪ child — nothing dropped (the never-delete rule).
    post_archive = set(_archive_bullets(parent)) | set(_archive_bullets(child_text))
    assert pre_archive <= post_archive, (
        "archive bullets were lost across the split:\n"
        f"missing: {sorted(pre_archive - post_archive)}"
    )


def test_child_page_is_dated_and_marked_relocated(tmp_path, monkeypatch):
    path = _seed_full_archive(tmp_path, monkeypatch)
    big = PillarState(
        name="gtm", summary="big",
        claims=_many_live_claims(gp._MAX_DRAFT_LINES + 50),
    )
    gp._write_pillar_draft(big, today="2026-08-01")

    child_text = open(gp._archive_child_path(path, 1), encoding="utf-8").read()
    assert "2026-08-01" in child_text                 # dated
    assert "relocated" in child_text.lower()          # explicitly a relocation
    assert "Archived claims" in child_text            # carries the archive bullets


# --------------------------------------------------------------------------- #
# A normal-sized draft must NOT split.                                          #
# --------------------------------------------------------------------------- #


def test_normal_draft_does_not_split(tmp_path, monkeypatch):
    monkeypatch.setattr(gp, "OUT_DIR", str(tmp_path))
    path = os.path.join(str(tmp_path), "pillar_gtm.md")
    p = PillarState(name="gtm", summary="small",
                    claims=[_claim("e1", "k", "A"), _claim("e2", "k2", "B")])
    gp._write_pillar_draft(p, today="2026-06-20")

    assert not os.path.isfile(gp._archive_child_path(path, 1))  # no child
    assert "moved to" not in open(path, encoding="utf-8").read()  # no pointer


# --------------------------------------------------------------------------- #
# Chained splits never clobber an earlier child.                               #
# --------------------------------------------------------------------------- #


def test_second_split_writes_a_second_child(tmp_path, monkeypatch):
    path = _seed_full_archive(tmp_path, monkeypatch)
    # first over-cap write -> child 01
    gp._write_pillar_draft(
        PillarState(name="gtm", summary="big-1",
                    claims=_many_live_claims(gp._MAX_DRAFT_LINES + 50)),
        today="2026-08-01",
    )
    assert os.path.isfile(gp._archive_child_path(path, 1))

    # pile the archive up again, then a second over-cap write -> child 02
    for i in range(gp._MAX_ARCHIVE_LINES + 5):
        gp._write_pillar_draft(
            PillarState(name="gtm", summary=f"r{i}",
                        claims=[_claim(f"x{i}", "launch_date", f"W_{i}")]),
            today="2026-09-01",
        )
    gp._write_pillar_draft(
        PillarState(name="gtm", summary="big-2",
                    claims=_many_live_claims(gp._MAX_DRAFT_LINES + 50)),
        today="2026-09-02",
    )

    # both children exist (the second did not clobber the first)
    assert os.path.isfile(gp._archive_child_path(path, 1))
    assert os.path.isfile(gp._archive_child_path(path, 2))
