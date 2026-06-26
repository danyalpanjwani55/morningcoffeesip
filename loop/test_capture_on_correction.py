"""Tests for the mid-session capture-on-correction entrypoint (plan item 3.1).

The capability already existed (``capture()`` is standalone, zero close-out
dependency); the only gap was a one-call convenience entrypoint an agent invokes
the instant the operator corrects a substantive error — and a doctrine pointer in
the ``pulse`` skill telling it to. These tests pin both halves:

  * ``capture_correction()`` appends a ``proposed`` delta IMMEDIATELY — it lands
    in the ledger as ``proposed``, surfaced as an open delta, with NO apply /
    close-out step in between;
  * it is a thin wrapper — the proposed-only guarantee and the recurrence
    escalation behavior come straight from ``capture()``;
  * the doctrine pointer (the entrypoint by name) is present in the pulse skill.

Deterministic; no network. Every brain write is isolated to a tmp_path via the
``brain_root=`` arg, exactly like test_skill_deltas.py.

Run: ``/usr/bin/python3 -B -m pytest -q`` (Apple python; bust the bytecode cache).
"""

from __future__ import annotations

import os

import pytest

import skill_deltas as sd

# The pulse skill that must carry the doctrine pointer (repo root is one up).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PULSE_SKILL = os.path.join(_REPO_ROOT, "skills", "pulse", "SKILL.md")


@pytest.fixture
def brain(tmp_path):
    """An isolated brain root for one test (every loop write lands here)."""
    return str(tmp_path / "brain")


def _correct(brain, **over):
    kw = dict(
        skill="pulse",
        root_cause="relayed an unverified categorical claim",
        what="verify a categorical claim against the primary record before relay",
        why="a confident wrong claim looks authoritative and propagates",
        owner="ops-lead",
        brain_root=brain,
    )
    kw.update(over)
    return sd.capture_correction(**kw)


# --------------------------------------------------------------------------- #
# the entrypoint appends a PROPOSED delta immediately (no close-out)          #
# --------------------------------------------------------------------------- #


def test_capture_correction_files_proposed_immediately(brain):
    d = _correct(brain)
    # it lands in the ledger as proposed — the instant it is called, no apply
    # / reject / close-out step in between.
    assert d.status == sd.PROPOSED
    assert d.is_open()
    # and it is queryable from the ledger as proposed (it persisted, not just a
    # return value) — the morning gate would surface exactly this one.
    fetched = sd.get_delta(d.id, brain)
    assert fetched is not None
    assert fetched.status == sd.PROPOSED
    assert [x.id for x in sd.open_deltas(brain)] == [d.id]


def test_capture_correction_defaults_anchor_for_in_conversation(brain):
    # mid-conversation there's no pulse/handoff slug yet, so the entrypoint
    # supplies a sensible in-the-moment anchor (capture() still requires one).
    d = _correct(brain)
    assert d.anchors == ("in-conversation-correction",)


def test_capture_correction_passes_explicit_anchor_through(brain):
    d = _correct(brain, anchor="2026-06-26-live-correction")
    assert d.anchors == ("2026-06-26-live-correction",)


def test_capture_correction_is_thin_wrapper_recurrence_escalates(brain):
    # behavior inherited from capture(): same skill + root cause ESCALATES the
    # one row rather than duplicating — proves it is a real wrapper, not a fork.
    first = _correct(brain)
    second = _correct(brain, anchor="later-correction")
    assert second.id == first.id
    assert len(sd.list_deltas(brain)) == 1
    assert second.recurrence == 2
    assert second.priority == "high"          # escalated one rung (med->high)
    assert second.status == sd.PROPOSED        # still operator-gated, never applied


# --------------------------------------------------------------------------- #
# the doctrine pointer is present in the pulse skill                          #
# --------------------------------------------------------------------------- #


def test_doctrine_pointer_present_in_pulse_skill():
    with open(_PULSE_SKILL, encoding="utf-8") as fh:
        body = fh.read()
    # the skill names the actual entrypoint to call the moment a correction lands.
    assert "capture_correction" in body
