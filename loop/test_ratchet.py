"""Tests for loop/ratchet — harvest a recurring miss into a CANDIDATE rule.

Deterministic; no network. Brain writes isolated to tmp_path via brain_root=.

Pins the contract (from ratchet-generalization-v1.md §2.3):
  * a miss recurring >= 2x earns a candidate rule; a 1x miss does NOT;
  * the candidate is APPENDED to the domain's ``## Candidate rules`` block and
    is NEVER promoted into the ratified list;
  * the candidate line carries a DATED provenance tag citing the occurrences;
  * the pass is IDEMPOTENT (re-running appends nothing new);
  * an APPLIED or REJECTED recurring miss does NOT ratchet (only an unaddressed
    open recurring miss does).

Run: ``/usr/bin/python3 -B -m pytest -q``.
"""

from __future__ import annotations

import os

import pytest

import ratchet
import skill_deltas as sd


@pytest.fixture
def brain(tmp_path):
    return str(tmp_path / "brain")


def _recurring_miss(brain, *, n=2, skill="pulse", root="relayed unverified claim"):
    """Capture a miss and escalate it to recurrence n (>=1 anchor each occurrence)."""
    d = sd.capture(
        skill=skill, root_cause=root,
        what="verify a categorical claim against the primary record before relay",
        why="a confident wrong claim propagates", owner="ops-lead",
        anchor=f"{skill}-occ-1", brain_root=brain,
    )
    for i in range(2, n + 1):
        d = sd.capture(
            skill=skill, root_cause=root,
            what="verify a categorical claim against the primary record before relay",
            why="a confident wrong claim propagates", owner="ops-lead",
            anchor=f"{skill}-occ-{i}", brain_root=brain,
        )
    return d


# --------------------------------------------------------------------------- #
# the >= 2x bar                                                               #
# --------------------------------------------------------------------------- #


def test_one_off_miss_does_not_ratchet(brain):
    _recurring_miss(brain, n=1)
    cands = ratchet.harvest_candidates(brain)
    assert cands == []                               # 1x is logged, not a rule


def test_recurring_miss_earns_a_candidate(brain):
    _recurring_miss(brain, n=2)
    cands = ratchet.harvest_candidates(brain)
    assert len(cands) == 1
    c = cands[0]
    assert c.recurrence == 2
    assert c.domain == "pulse"
    # dated provenance tag + cited occurrences (verify-before-relay)
    assert "recurred 2x" in c.rule_line
    assert "pulse-occ-1" in c.rule_line and "pulse-occ-2" in c.rule_line


# --------------------------------------------------------------------------- #
# never promote — only append to the candidate block                         #
# --------------------------------------------------------------------------- #


def test_run_appends_to_candidate_block_never_ratified(brain):
    _recurring_miss(brain, n=2)
    appended = ratchet.run(brain)
    assert len(appended) == 1

    path = ratchet.rules_path("pulse", brain)
    assert os.path.isfile(path)
    with open(path) as fh:
        body = fh.read()

    # the candidate sits UNDER the candidate header, and the ratified list is
    # still the empty seed — the pass promoted nothing.
    cand_idx = body.index(ratchet._CANDIDATE_HEADER)
    ratified_idx = body.index("## Ratified rules")
    assert ratified_idx < cand_idx                   # ratified section is above
    ratified_block = body[ratified_idx:cand_idx]
    assert "none yet" in ratified_block              # still empty — not promoted
    # the rule line is in the candidate block
    assert body.index("verify a categorical claim") > cand_idx


def test_run_is_idempotent(brain):
    _recurring_miss(brain, n=2)
    first = ratchet.run(brain)
    assert len(first) == 1
    path = ratchet.rules_path("pulse", brain)
    with open(path) as fh:
        body_after_first = fh.read()

    second = ratchet.run(brain)                       # re-run
    assert second == []                               # nothing new appended
    with open(path) as fh:
        body_after_second = fh.read()
    assert body_after_first == body_after_second      # file unchanged


# --------------------------------------------------------------------------- #
# addressed misses don't ratchet                                             #
# --------------------------------------------------------------------------- #


def test_applied_recurring_miss_does_not_ratchet(brain):
    d = _recurring_miss(brain, n=2)
    target = os.path.join(brain, "skill.md")
    os.makedirs(brain, exist_ok=True)
    with open(target, "w") as fh:
        fh.write("x\n")
    sd.apply(d.id, target, brain_root=brain)          # the fix is in the skill
    assert ratchet.harvest_candidates(brain) == []    # so it doesn't ratchet


def test_rejected_recurring_miss_does_not_ratchet(brain):
    d = _recurring_miss(brain, n=2)
    sd.reject(d.id, "declined by operator", brain_root=brain)
    assert ratchet.harvest_candidates(brain) == []


# --------------------------------------------------------------------------- #
# two domains -> two blocks                                                   #
# --------------------------------------------------------------------------- #


def test_distinct_domains_get_distinct_blocks(brain):
    _recurring_miss(brain, n=2, skill="pulse", root="unverified claim")
    _recurring_miss(brain, n=2, skill="coffee", root="watched the log not the work")
    appended = ratchet.run(brain)
    assert len(appended) == 2
    assert os.path.isfile(ratchet.rules_path("pulse", brain))
    assert os.path.isfile(ratchet.rules_path("coffee", brain))


def test_candidate_id_is_deterministic(brain):
    _recurring_miss(brain, n=2)
    c1 = ratchet.harvest_candidates(brain)[0]
    c2 = ratchet.harvest_candidates(brain)[0]
    assert c1.candidate_id == c2.candidate_id         # stable across calls
