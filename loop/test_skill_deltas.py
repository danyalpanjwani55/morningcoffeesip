"""Tests for loop/skill_deltas — the close-the-loop ledger.

Deterministic; no network. Every brain write is isolated to a tmp_path via the
``brain_root=`` arg (the modules resolve paths through mcs_paths but accept an
explicit override, so tests never touch a real brain).

Pins the contract:
  * capture() produces a PROPOSED proposal — never auto-applies;
  * the recurrence rule ESCALATES (priority++ / recurrence++ / anchor appended)
    rather than filing a duplicate; a recurrence after a resolved fix re-opens it;
  * apply() archives a PRE-IMAGE (the revert point) then flips to applied;
  * revert() restores the target file from the pre-image with ONE call, and
    archive-don't-delete keeps the pre-image afterward;
  * reject() needs a reason and stops re-surfacing;
  * the JSONL is append-only (line count only grows);
  * a malicious id can't write outside the loop root.

Run: ``/usr/bin/python3 -B -m pytest -q`` (Apple python; bust the bytecode cache).
"""

from __future__ import annotations

import os

import pytest

import skill_deltas as sd


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def brain(tmp_path):
    """An isolated brain root for one test (every loop write lands here)."""
    return str(tmp_path / "brain")


def _capture_one(brain, **over):
    kw = dict(
        skill="pulse",
        root_cause="relayed an unverified categorical claim",
        what="verify a categorical claim against the primary record before relay",
        why="a confident wrong claim looks authoritative and propagates",
        owner="ops-lead",
        anchor="pulse-2026-06-21",
        priority="med",
        brain_root=brain,
    )
    kw.update(over)
    return sd.capture(**kw)


# --------------------------------------------------------------------------- #
# capture — proposes, never applies                                           #
# --------------------------------------------------------------------------- #


def test_capture_is_proposed_never_applied(brain):
    d = _capture_one(brain)
    assert d.status == sd.PROPOSED
    assert d.is_open()
    assert d.recurrence == 1
    assert d.anchors == ("pulse-2026-06-21",)
    # it shows up as the single open delta the morning gate would surface
    opens = sd.open_deltas(brain)
    assert [x.id for x in opens] == [d.id]


def test_capture_requires_anchor(brain):
    with pytest.raises(ValueError):
        _capture_one(brain, anchor="")


def test_capture_writes_operator_markdown_view(brain):
    _capture_one(brain)
    md = sd.render_markdown(brain)
    assert "In plain terms" in md
    assert "Open (awaiting the operator): 1" in md
    assert os.path.isfile(sd.ledger_md_path(brain))


# --------------------------------------------------------------------------- #
# recurrence — escalates, never duplicates                                    #
# --------------------------------------------------------------------------- #


def test_recurrence_escalates_not_duplicates(brain):
    first = _capture_one(brain)
    # same skill + same root cause, new anchor + occurrence
    second = _capture_one(brain, anchor="pulse-2026-06-22")
    assert second.id == first.id                       # SAME row, not a new one
    assert len(sd.list_deltas(brain)) == 1             # exactly one delta exists
    assert second.recurrence == 2                      # bumped
    assert second.priority == "high"                   # escalated one rung (med->high)
    assert "pulse-2026-06-22" in second.anchors        # anchor appended


def test_distinct_root_cause_is_a_new_row(brain):
    a = _capture_one(brain)
    b = _capture_one(brain, root_cause="a totally different miss", anchor="x1")
    assert b.id != a.id
    assert len(sd.list_deltas(brain)) == 2


def test_recurrence_after_resolved_fix_reopens(brain):
    d = _capture_one(brain)
    target = os.path.join(brain, "skill.md")
    os.makedirs(brain, exist_ok=True)
    with open(target, "w") as fh:
        fh.write("ORIGINAL\n")
    sd.apply(d.id, target, brain_root=brain)
    assert sd.get_delta(d.id, brain).status == sd.APPLIED
    # the same miss recurs -> the applied row must re-open (the fix didn't hold)
    again = _capture_one(brain, anchor="pulse-later")
    assert again.id == d.id
    assert again.status == sd.PROPOSED                 # re-opened
    assert again.recurrence == 2


# --------------------------------------------------------------------------- #
# apply — pre-image archived, then applied                                    #
# --------------------------------------------------------------------------- #


def test_apply_archives_preimage_then_flips(brain):
    d = _capture_one(brain)
    target = os.path.join(brain, "skill.md")
    os.makedirs(brain, exist_ok=True)
    with open(target, "w") as fh:
        fh.write("BEFORE THE EDIT\n")

    applied = sd.apply(d.id, target, registry_id="SD-1", brain_root=brain)
    assert applied.status == sd.APPLIED
    assert applied.resolution == "SD-1"
    # the pre-image exists, is under the loop root, and holds the BEFORE content.
    # Compare realpath-to-realpath (resolve BOTH sides) so the confinement check
    # is independent of /tmp-vs-/private/tmp symlink layout — the same property
    # the production guard (_assert_under_loop_root) enforces.
    assert applied.preimage and os.path.isfile(applied.preimage)
    assert os.path.realpath(applied.preimage).startswith(
        os.path.realpath(sd.preimage_dir(brain)) + os.sep)
    with open(applied.preimage) as fh:
        assert fh.read() == "BEFORE THE EDIT\n"


def test_apply_missing_target_raises(brain):
    d = _capture_one(brain)
    with pytest.raises(FileNotFoundError):
        sd.apply(d.id, os.path.join(brain, "nope.md"), brain_root=brain)


def test_cannot_apply_twice(brain):
    d = _capture_one(brain)
    target = os.path.join(brain, "skill.md")
    os.makedirs(brain, exist_ok=True)
    with open(target, "w") as fh:
        fh.write("x\n")
    sd.apply(d.id, target, brain_root=brain)
    with pytest.raises(ValueError):
        sd.apply(d.id, target, brain_root=brain)   # already applied


# --------------------------------------------------------------------------- #
# revert — one command, restore from pre-image (archive-don't-delete)         #
# --------------------------------------------------------------------------- #


def test_revert_restores_from_preimage(brain):
    d = _capture_one(brain)
    target = os.path.join(brain, "skill.md")
    os.makedirs(brain, exist_ok=True)
    with open(target, "w") as fh:
        fh.write("ORIGINAL CONTENT\n")
    applied = sd.apply(d.id, target, brain_root=brain)
    preimage = applied.preimage

    # the operator/owner makes the edit to the target file (the "apply" effect)
    with open(target, "w") as fh:
        fh.write("EDITED — the applied change\n")

    reverted = sd.revert(d.id, brain_root=brain)
    # ONE command restored the file from the pre-image
    with open(target) as fh:
        assert fh.read() == "ORIGINAL CONTENT\n"
    # status back to proposed (so it can be re-applied)
    assert reverted.status == sd.PROPOSED
    # archive-don't-delete: the pre-image is STILL there after revert
    assert os.path.isfile(preimage)


def test_revert_requires_applied(brain):
    d = _capture_one(brain)
    with pytest.raises(ValueError):
        sd.revert(d.id, brain_root=brain)          # still proposed, nothing to revert


# --------------------------------------------------------------------------- #
# reject / supersede — real outcomes that stop re-surfacing                   #
# --------------------------------------------------------------------------- #


def test_reject_needs_reason_and_closes(brain):
    d = _capture_one(brain)
    with pytest.raises(ValueError):
        sd.reject(d.id, "", brain_root=brain)
    out = sd.reject(d.id, "duplicate of an existing rule", brain_root=brain)
    assert out.status == sd.REJECTED
    assert out.resolution == "duplicate of an existing rule"
    assert sd.open_deltas(brain) == []             # no longer surfaced


def test_supersede_names_target_and_closes(brain):
    d = _capture_one(brain)
    out = sd.supersede(d.id, "sd-bigger-0000", brain_root=brain)
    assert out.status == sd.SUPERSEDED
    assert "sd-bigger-0000" in out.resolution
    assert sd.open_deltas(brain) == []


# --------------------------------------------------------------------------- #
# append-only integrity + confinement                                         #
# --------------------------------------------------------------------------- #


def test_ledger_is_append_only(brain):
    d = _capture_one(brain)
    target = os.path.join(brain, "skill.md")
    os.makedirs(brain, exist_ok=True)
    with open(target, "w") as fh:
        fh.write("x\n")

    def _lines():
        with open(sd.ledger_path(brain)) as fh:
            return fh.readlines()

    n0 = len(_lines())
    sd.apply(d.id, target, brain_root=brain)
    n1 = len(_lines())
    sd.revert(d.id, brain_root=brain)
    n2 = len(_lines())
    # the line count only ever GROWS (no in-place rewrite) — append-only
    assert n0 < n1 < n2


def test_malicious_id_cannot_escape_loop_root(brain, tmp_path):
    # a capture whose skill slug would try to traverse out must still write a
    # pre-image confined under the loop root — apply with a target outside is
    # fine (the target is the SKILL file), but the pre-image archive name is
    # derived from the id+basename and confined.
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n")
    d = _capture_one(brain, skill="../../escape")
    # the id is slug-sanitized, so the pre-image path stays under the loop root
    # (realpath both sides — symlink-layout-independent).
    applied = sd.apply(d.id, str(outside), brain_root=brain)
    assert os.path.realpath(applied.preimage).startswith(
        os.path.realpath(sd.loop_root(brain)) + os.sep)
