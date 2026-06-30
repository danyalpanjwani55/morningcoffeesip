"""Tests for loop Lane C — the per-agent journal + the graduation gate.

These pin the four DONE-BAR properties of the learning-loop v2 journal lane:

  1. a fold writes a ``parse_journal_entry``-parseable JournalEntry into the
     agent's journal (the merged lesson -> delta -> review -> next-time chain);
  2. a symptom that recurs (2nd time, hot ∪ archive) WITH a CONCUR review flips
     ``graduation`` to ``ready`` — a SURFACED PROPOSAL, never auto-applied;
  3. a concept restamp APPENDS a dated history line WITHOUT losing the prior one
     (supersede-with-archive: history grows monotonically);
  4. NONE of this writes or creates a file under ``skills/`` — graduation is a
     proposal the operator applies, not an auto-edit. (Proposals-only hard limit.)

Plus: the ``skill_deltas.capture_journal`` wrapper graduates on recurrence+CONCUR
while leaving its underlying delta ``proposed``; and the hot journal rolls its
oldest entry to ``journal-archive/`` (archive-don't-delete) when it gets large.

Deterministic; no network. Every brain write is isolated to a tmp_path via
``brain_root=``. The skills-untouched guard hashes the REAL ``skills/`` tree
before/after, so a regression that auto-edits a skill fails loudly.

Run: ``/usr/bin/python3 -B -m pytest -q`` (Apple python; bust the bytecode cache).
"""

from __future__ import annotations

import hashlib
import os

import pytest

import fold as F
import skill_deltas as sd
from journal_schema import (
    ConceptState,
    parse_journal_entry,
    render_concept_state,
)


# --------------------------------------------------------------------------- #
# Fixtures + helpers                                                           #
# --------------------------------------------------------------------------- #


@pytest.fixture
def brain(tmp_path):
    """An isolated brain root (every wiki/journal/concept write lands here)."""
    return str(tmp_path / "brain")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _journal_entries(agent: str, brain) -> list:
    """Every parseable JournalEntry in the agent's hot journal."""
    path = F.agent_journal_path(agent, brain)
    if not os.path.isfile(path):
        return []
    return F._parse_entries(_read(path))


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKILLS_DIR = os.path.join(_REPO_ROOT, "skills")


def _skills_fingerprint() -> dict[str, str]:
    """A content hash of every file under the REAL ``skills/`` tree. Comparing
    this before/after a fold proves the proposals-only guarantee: graduation must
    NEVER create or modify a skill file."""
    fp: dict[str, str] = {}
    for root, _dirs, files in os.walk(_SKILLS_DIR):
        for name in files:
            p = os.path.join(root, name)
            with open(p, "rb") as fh:
                fp[p] = hashlib.sha256(fh.read()).hexdigest()
    return fp


# A journal-bearing pulse: carries the v2 fields the fold folds into an entry.
def _journal_pulse(pid: str, *, agent="ops-lead", date="2026-06-21",
                   symptom="quoted a number from memory without opening the source",
                   review="", concepts=()) -> F.Pulse:
    return F.Pulse(
        pulse_id=pid, date=date, agents=(agent,), tier=F.POINTER,
        lesson="I stated a stale figure; the source says otherwise",
        title="stale-figure recall",
        worked_on=["answered a question", "read the thread"],
        understood=["the figure changed last month"],
        symptom=symptom,
        proposed_delta="open the owning concept + its source before quoting a number",
        review=review,
        next_time="route to the concept and read the primary before asserting a number",
        concepts_touched=concepts,
    )


# --------------------------------------------------------------------------- #
# 1. the fold writes a PARSEABLE journal entry                                 #
# --------------------------------------------------------------------------- #


def test_fold_writes_a_parseable_journal_entry(brain):
    res = F.fold([_journal_pulse("p1")], brain_root=brain, today="2026-06-22")
    assert res.journal_entries_written["ops-lead"] == 1

    jpath = F.agent_journal_path("ops-lead", brain)
    assert os.path.isfile(jpath)
    entries = _journal_entries("ops-lead", brain)
    assert len(entries) == 1

    je = entries[0]                       # round-trips through the schema parser
    assert je.agent == "ops-lead"
    assert je.n == 1
    assert je.date == "2026-06-22"
    assert je.lesson.startswith("I stated a stale figure")
    assert je.symptom.startswith("quoted a number from memory")
    assert je.proposed_delta.startswith("open the owning concept")
    assert je.next_time                    # the one-thing-different is carried
    # a first, never-before-seen symptom does NOT graduate
    assert je.graduation == "none"


def test_journal_persists_and_numbers_across_runs(brain):
    F.fold([_journal_pulse("p1", date="2026-06-21")], brain_root=brain, today="2026-06-22")
    F.fold([_journal_pulse("p2", date="2026-06-23",
                           symptom="a different miss entirely")],
           brain_root=brain, today="2026-06-24")
    entries = _journal_entries("ops-lead", brain)
    assert [e.n for e in entries] == [1, 2]      # ids continue, never reset
    # newest at the bottom (append-only growth, event order)
    assert entries[0].date == "2026-06-22" and entries[1].date == "2026-06-24"


def test_pointer_pulse_with_no_journal_fields_still_writes_an_entry(brain):
    # a plain pointer pulse (no symptom/review/concepts) still gets a minimal,
    # parseable journal entry — the journal is per-agent-per-fold, not opt-in.
    res = F.fold([F.Pulse("p0", "2026-06-21", ("ops-lead",), F.POINTER, "a bare lesson")],
                 brain_root=brain, today="2026-06-22")
    assert res.journal_entries_written["ops-lead"] == 1
    je = _journal_entries("ops-lead", brain)[0]
    assert je.lesson == "a bare lesson"
    assert je.symptom == "n/a"            # absent symptom -> the schema sentinel
    assert je.graduation == "none"


# --------------------------------------------------------------------------- #
# 2. recurrence + CONCUR -> graduation 'ready' (a SURFACED PROPOSAL)           #
# --------------------------------------------------------------------------- #


def test_repeated_symptom_plus_concur_graduates_to_ready(brain):
    sym = "quoting an SLA without opening the policy concept"
    # 1st occurrence — no recurrence yet, so NOT ready even though it's reviewed.
    F.fold([_journal_pulse("p1", date="2026-06-21", symptom=sym,
                           review="CONCUR by ops-twin")],
           brain_root=brain, today="2026-06-22")
    first = _journal_entries("ops-lead", brain)[0]
    assert first.graduation == "none"

    # 2nd occurrence of the SAME symptom WITH a CONCUR review -> graduates.
    res = F.fold([_journal_pulse("p2", date="2026-06-23", symptom=sym,
                                 review="CONCUR by ops-twin")],
                 brain_root=brain, today="2026-06-24")
    second = _journal_entries("ops-lead", brain)[1]
    assert second.graduation == F.GRADUATION_READY          # "ready"
    # the run SURFACES the graduation proposal (agent:symptom) for the operator
    assert any(g.endswith(sym) and g.startswith("ops-lead:")
               for g in res.graduations_proposed)


def test_recurrence_without_concur_does_not_graduate(brain):
    sym = "shipped a claim without a source anchor"
    F.fold([_journal_pulse("p1", date="2026-06-21", symptom=sym, review="CONCUR by t")],
           brain_root=brain, today="2026-06-22")
    # 2nd time, but the review REFUTES -> recurrence is surfaced, NOT graduated.
    res = F.fold([_journal_pulse("p2", date="2026-06-23", symptom=sym,
                                 review="REFUTE by t; residuals: the claim was fine")],
                 brain_root=brain, today="2026-06-24")
    second = _journal_entries("ops-lead", brain)[1]
    assert second.graduation != F.GRADUATION_READY
    assert "awaiting CONCUR" in second.graduation        # honest: surfaced, not ready
    assert res.graduations_proposed == []                # nothing graduated


def test_first_time_concur_does_not_graduate(brain):
    # a CONCUR on the FIRST sighting must not graduate (graduation needs RECURRENCE)
    res = F.fold([_journal_pulse("p1", review="CONCUR by ops-twin")],
                 brain_root=brain, today="2026-06-22")
    assert _journal_entries("ops-lead", brain)[0].graduation == "none"
    assert res.graduations_proposed == []


def test_recurrence_detection_reaches_into_the_archive(brain):
    # an OLD sighting that has rolled to journal-archive/ must still count as the
    # 1st occurrence, so a later repeat (+ CONCUR) graduates (hot ∪ archive search).
    sym = "asserted a competitor fact from memory"
    agent = "ops-lead"
    arch_dir = F.agent_journal_archive_dir(agent, brain)
    os.makedirs(arch_dir, exist_ok=True)
    # hand-place a parseable historical entry in the archive (a prior sighting).
    from journal_schema import JournalEntry, render_journal_entry
    old = JournalEntry(agent=agent, n=1, date="2026-05-01", title="old sighting",
                       symptom=sym, review="CONCUR by t")
    with open(os.path.join(arch_dir, "2026-05.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# {agent} — journal archive (2026-05)\n\n"
                 + render_journal_entry(old) + "\n")

    # now a fresh fold with the SAME symptom + CONCUR — it's the 2nd sighting.
    res = F.fold([_journal_pulse("p2", date="2026-06-23", symptom=sym,
                                 review="CONCUR by t")],
                 brain_root=brain, today="2026-06-24")
    hot = _journal_entries(agent, brain)[0]
    assert hot.graduation == F.GRADUATION_READY
    assert hot.n == 2                                    # numbering continued past the archive
    assert res.graduations_proposed


# --------------------------------------------------------------------------- #
# 3. concept restamp APPENDS history, never loses the prior line               #
# --------------------------------------------------------------------------- #


def _seed_concept(agent: str, slug: str, brain, *, history=None) -> str:
    """Write a concept STATE file with one recurrent-state line + optional history,
    in the canonical schema format, and return its path."""
    cs = ConceptState(
        slug=slug, agent=agent, state_updated="2026-06-01",
        recurrent_state=["the refund SLA is 48h · high · sources/02#L4"],
        history=list(history or []),
        overview="Support owns refunds and the escalation ladder.",
        source_docs=["sources/02 the refund policy · refund#L4"],
    )
    path = F.agent_concept_path(agent, slug, brain)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_concept_state(cs))
    return path


def test_concept_restamp_appends_history_without_losing_prior(brain):
    agent, slug = "ops-lead", "refund-policy"
    prior = "2026-05-10 was 72h → changed to 48h because sources/02#L4"
    cpath = _seed_concept(agent, slug, brain, history=[prior])

    res = F.fold([_journal_pulse("p1", agent=agent, date="2026-06-21",
                                 concepts=(slug,))],
                 brain_root=brain, today="2026-06-22")
    assert f"{agent}/{slug}" in res.concepts_restamped

    # re-parse the restamped concept: the PRIOR history line survives AND a new
    # dated line was appended; state_updated advanced to the fold date.
    from journal_schema import parse_concept_state
    cs = parse_concept_state(_read(cpath))
    assert prior in cs.history                            # prior line NOT lost
    assert len(cs.history) == 2                           # exactly one appended
    assert cs.history[-1].startswith("2026-06-22")        # the new dated line
    assert "p1" in cs.history[-1]                         # carries its source pulse
    assert cs.state_updated == "2026-06-22"               # refreshed
    # the recurrent-state line is untouched (we append history, not rewrite state)
    assert cs.recurrent_state == ["the refund SLA is 48h · high · sources/02#L4"]


def test_two_folds_append_two_history_lines_monotonically(brain):
    agent, slug = "ops-lead", "pricing"
    cpath = _seed_concept(agent, slug, brain)
    F.fold([_journal_pulse("p1", agent=agent, date="2026-06-21", concepts=(slug,))],
           brain_root=brain, today="2026-06-22")
    F.fold([_journal_pulse("p2", agent=agent, date="2026-06-23", concepts=(slug,))],
           brain_root=brain, today="2026-06-24")
    from journal_schema import parse_concept_state
    cs = parse_concept_state(_read(cpath))
    assert len(cs.history) == 2                           # both appended, none lost
    assert cs.history[0].startswith("2026-06-22")
    assert cs.history[1].startswith("2026-06-24")


def test_restamp_is_a_noop_when_concept_file_absent(brain):
    # the fold updates only EXISTING concept STATE files (the wiki builder owns
    # creation); a touched concept with no file yet is a clean no-op, no crash.
    res = F.fold([_journal_pulse("p1", concepts=("does-not-exist-yet",))],
                 brain_root=brain, today="2026-06-22")
    assert res.concepts_restamped == []                  # nothing restamped
    # but the journal entry STILL recorded the concept it touched
    je = _journal_entries("ops-lead", brain)[0]
    assert "does-not-exist-yet" in je.concept_touched


# --------------------------------------------------------------------------- #
# 4. PROPOSALS-ONLY — no skill file is created or modified by any of this       #
# --------------------------------------------------------------------------- #


def test_graduation_never_touches_any_skill_file(brain):
    # the hard limit: graduation is a SURFACED PROPOSAL — it must NOT create or
    # modify a single file under skills/. Snapshot the real tree, run a fold that
    # GRADUATES, and assert the tree is byte-identical afterward.
    before = _skills_fingerprint()
    assert before, "expected a non-empty skills/ tree to guard"

    sym = "quoting a policy number without opening the concept"
    F.fold([_journal_pulse("p1", date="2026-06-21", symptom=sym, review="CONCUR by t")],
           brain_root=brain, today="2026-06-22")
    res = F.fold([_journal_pulse("p2", date="2026-06-23", symptom=sym,
                                 review="CONCUR by t", concepts=())],
                 brain_root=brain, today="2026-06-24")
    # it really did graduate (so we're proving the guard against a LIVE graduation)
    assert res.graduations_proposed, "test setup must produce a graduation to be meaningful"
    assert _journal_entries("ops-lead", brain)[1].graduation == F.GRADUATION_READY

    after = _skills_fingerprint()
    assert after == before, "PROPOSALS-ONLY VIOLATION: a skill file changed during a fold"


# --------------------------------------------------------------------------- #
# the skill_deltas journal-aware wrapper (capture_journal)                     #
# --------------------------------------------------------------------------- #


def _capture_journal(brain, **over):
    kw = dict(
        skill="pulse",
        root_cause="relayed an unverified categorical claim",
        what="verify a categorical claim against the primary record before relay",
        why="a confident wrong claim looks authoritative and propagates",
        owner="ops-lead",
        anchor="pulse-2026-06-21",
        lesson="I asserted X categorically without checking the record",
        symptom="categorical claim without checking the primary record",
        review="",
        brain_root=brain,
    )
    kw.update(over)
    return sd.capture_journal(**kw)


def test_capture_journal_reuses_capture_and_stays_proposed(brain):
    jd = _capture_journal(brain)
    assert jd.status == sd.PROPOSED                       # reused capture: proposed
    assert jd.graduation == sd.GRAD_NONE                  # 1st sighting -> not ready
    # the underlying delta is a real ledger row the morning gate would surface
    assert [d.id for d in sd.open_deltas(brain)] == [jd.id]


def test_capture_journal_graduates_on_recurrence_plus_concur(brain):
    first = _capture_journal(brain)
    assert first.graduation == sd.GRAD_NONE
    # SAME skill+root_cause (recurrence -> escalates the SAME row) WITH a CONCUR.
    second = _capture_journal(brain, anchor="pulse-2026-06-22",
                              review="CONCUR by software-twin")
    assert second.id == first.id                          # escalated, not duplicated
    assert second.recurrence == 2
    assert second.graduation == sd.GRAD_READY             # recurrence + CONCUR
    # graduation is a PROPOSAL — the underlying delta is STILL proposed (not applied)
    assert second.status == sd.PROPOSED
    assert sd.get_delta(second.id, brain).status == sd.PROPOSED


def test_capture_journal_recurrence_without_concur_does_not_graduate(brain):
    _capture_journal(brain)
    second = _capture_journal(brain, anchor="x2", review="REFUTE by t; residuals: none")
    assert second.recurrence == 2
    assert second.graduation == sd.GRAD_NONE              # recurred but no CONCUR


def test_review_concurs_only_on_concur_head():
    assert sd.review_concurs("CONCUR by someone")
    assert sd.review_concurs("concur")
    assert not sd.review_concurs("REFUTE by someone; residuals: x")
    assert not sd.review_concurs("")
    assert not sd.review_concurs("n/a")


# --------------------------------------------------------------------------- #
# archive roll — hot journal stays bounded; rolled entries are grep-reachable   #
# --------------------------------------------------------------------------- #


def test_hot_journal_rolls_oldest_to_archive_when_large(brain):
    agent = "ops-lead"
    cap = F._HOT_JOURNAL_MAX
    # write cap+2 entries via folds; the hot file must stay <= cap, the overflow
    # must land in journal-archive/ (archive-don't-delete), and numbering stays
    # globally unique across the split.
    for i in range(cap + 2):
        F.fold([_journal_pulse(f"p{i}", date="2026-06-21",
                               symptom=f"distinct miss number {i}")],
               brain_root=brain, today="2026-06-22")

    hot = _journal_entries(agent, brain)
    assert len(hot) <= cap, "the hot journal must stay bounded after rolling"

    arch_dir = F.agent_journal_archive_dir(agent, brain)
    assert os.path.isdir(arch_dir), "overflow must roll to journal-archive/"
    archived = []
    for name in sorted(os.listdir(arch_dir)):
        if name.endswith(".md"):
            archived += F._parse_entries(_read(os.path.join(arch_dir, name)))
    assert archived, "the oldest entries must be archived, not deleted"

    # hot ∪ archive == every entry, with globally-unique, contiguous ids 1..cap+2.
    all_ns = sorted(e.n for e in hot + archived)
    assert all_ns == list(range(1, cap + 3))             # nothing lost, none reused
    # the archived ones are the OLDEST (lowest n); the hot ones are the newest.
    assert max(e.n for e in archived) < min(e.n for e in hot)


def test_writes_confined_to_wiki_root(brain):
    # the journal + concept writes obey the same confinement the log does.
    _seed_concept("ops-lead", "c", brain)
    F.fold([_journal_pulse("p1", concepts=("c",))], brain_root=brain, today="2026-06-22")
    jpath = os.path.realpath(F.agent_journal_path("ops-lead", brain))
    cpath = os.path.realpath(F.agent_concept_path("ops-lead", "c", brain))
    root = os.path.realpath(F.wiki_root(brain))
    assert jpath.startswith(root) and cpath.startswith(root)
