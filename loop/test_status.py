"""Tests for loop/status — the per-agent boot roll-up (Lane D).

Deterministic; no network. Every brain write is isolated to a tmp_path via the
``brain_root=`` arg (status + skill_deltas resolve through mcs_paths but accept
an explicit override, so tests never touch a real brain).

Pins the Lane-D done-bar:
  * ``build_status`` reflects the LATEST journal entry's worked-on + the recent
    next-time lines + the correct ready/owed counts;
  * rebuilding from the same inputs is IDEMPOTENT (byte-identical, no wall-clock);
  * an agent with NO journal yields a clean status (never a crash);
  * blockers derive from a REFUTE review (and "none" is said explicitly);
  * a malformed journal block is tolerated (skipped), not fatal;
  * the writer is confined under the wiki root and writes the same bytes
    build_status returns.

Run: ``/usr/bin/python3 -B -m pytest -q`` (Apple python; bust the bytecode cache).
"""

from __future__ import annotations

import os

import pytest

import skill_deltas as sd
import status as st
from journal_schema import JournalEntry, render_journal_entry


# --------------------------------------------------------------------------- #
# Fixtures + helpers                                                          #
# --------------------------------------------------------------------------- #


@pytest.fixture
def brain(tmp_path):
    """An isolated brain root for one test (every wiki/ledger write lands here)."""
    return str(tmp_path / "brain")


def _entry(agent="specialist-a", n=1, **over) -> JournalEntry:
    kw = dict(
        agent=agent,
        n=n,
        date="2026-06-29",
        title="a session",
        worked_on=["the onboarding flow"],
        understood=["the gate is the only write path"],
        lesson="none",
        symptom="n/a",
        proposed_delta="none",
        review="n/a",
        next_time="read the concept state before re-deriving",
        concept_touched=["onboarding"],
        graduation="none",
    )
    kw.update(over)
    return JournalEntry(**kw)


def _write_journal(brain, agent, entries) -> str:
    """Write rendered entries to <brain>/wiki/<agent>/journal.md (as the sibling
    Lane C would), newest-last."""
    path = st.agent_journal_path(agent, brain)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(render_journal_entry(e) for e in entries))
    return path


# --------------------------------------------------------------------------- #
# working-on + next-time reflect the LATEST entry                             #
# --------------------------------------------------------------------------- #


def test_status_reflects_latest_worked_on(brain):
    agent = "specialist-a"
    _write_journal(brain, agent, [
        _entry(n=1, worked_on=["the old thing"], next_time="old next-time"),
        _entry(n=2, worked_on=["the NEW thing"], next_time="new next-time"),
    ])
    out = st.build_status(agent, brain)
    assert "## Working on" in out
    assert "the NEW thing" in out
    # the latest entry's worked-on wins; the superseded one is not the headline
    working_section = out.split("## Working on", 1)[1].split("##", 1)[0]
    assert "the NEW thing" in working_section
    assert "the old thing" not in working_section
    # the as-of marker is the latest entry (deterministic, not wall-clock)
    assert "as of J-specialist-a-002" in out


def test_status_surfaces_recent_next_time_newest_first(brain):
    agent = "specialist-a"
    _write_journal(brain, agent, [
        _entry(n=1, next_time="first lesson"),
        _entry(n=2, next_time="second lesson"),
        _entry(n=3, next_time="third lesson"),
    ])
    out = st.build_status(agent, brain)
    section = out.split("## Top next-time", 1)[1]
    # newest first
    assert section.index("third lesson") < section.index("second lesson") < section.index("first lesson")


def test_latest_is_by_entry_number_not_file_order(brain):
    """Even if the sibling lane wrote entries out of order, 'latest' = max n."""
    agent = "specialist-a"
    _write_journal(brain, agent, [
        _entry(n=2, worked_on=["the NEW thing"]),
        _entry(n=1, worked_on=["the old thing"]),  # written last, but older
    ])
    out = st.build_status(agent, brain)
    working_section = out.split("## Working on", 1)[1].split("##", 1)[0]
    assert "the NEW thing" in working_section
    assert "the old thing" not in working_section


# --------------------------------------------------------------------------- #
# blockers — derived from a REFUTE review; "none" said explicitly             #
# --------------------------------------------------------------------------- #


def test_blocker_derived_from_refute_review(brain):
    agent = "specialist-a"
    _write_journal(brain, agent, [
        _entry(n=1, review="CONCUR by reviewer-x"),
        _entry(n=2, review="REFUTE by reviewer-y; residuals: the path still 404s"),
    ])
    out = st.build_status(agent, brain)
    section = out.split("## Open blockers", 1)[1].split("##", 1)[0]
    assert "REFUTE by reviewer-y" in section
    assert "the path still 404s" in section


def test_no_blockers_says_so(brain):
    agent = "specialist-a"
    _write_journal(brain, agent, [_entry(n=1, review="CONCUR by reviewer-x")])
    out = st.build_status(agent, brain)
    section = out.split("## Open blockers", 1)[1].split("##", 1)[0]
    assert "none" in section.lower()


# --------------------------------------------------------------------------- #
# counts — ready-to-graduate (journal) + open deltas owed (ledger)            #
# --------------------------------------------------------------------------- #


def test_ready_count_from_graduation_flag(brain):
    agent = "specialist-a"
    _write_journal(brain, agent, [
        _entry(n=1, graduation="none"),
        _entry(n=2, graduation="ready"),
        _entry(n=3, graduation="ready (recurrence-2x → CONCUR)"),
    ])
    out = st.build_status(agent, brain)
    assert "ready to graduate (proposals — the operator applies): 2" in out


def test_owed_count_from_ledger_filtered_by_owner(brain):
    agent = "specialist-a"
    _write_journal(brain, agent, [_entry(n=1)])
    # two open deltas owned by this agent, one owned by someone else
    sd.capture(skill="pulse", root_cause="cause one", what="x", why="y",
               owner="specialist-a", anchor="a1", brain_root=brain)
    sd.capture(skill="pulse", root_cause="cause two", what="x", why="y",
               owner="specialist-a", anchor="a2", brain_root=brain)
    sd.capture(skill="pulse", root_cause="cause three", what="x", why="y",
               owner="specialist-b", anchor="a3", brain_root=brain)
    out = st.build_status(agent, brain)
    assert "open skill-deltas owed to this agent: 2" in out


def test_applied_delta_not_counted_as_owed(brain, tmp_path):
    """Only OPEN (proposed) deltas are owed — an applied one drops off the count."""
    agent = "specialist-a"
    _write_journal(brain, agent, [_entry(n=1)])
    d = sd.capture(skill="pulse", root_cause="resolved cause", what="x", why="y",
                   owner="specialist-a", anchor="a1", brain_root=brain)
    # apply it (needs a real target file to pre-image)
    target = tmp_path / "skillfile.md"
    target.write_text("# a skill\n", encoding="utf-8")
    sd.apply(d.id, str(target), brain_root=brain)
    out = st.build_status(agent, brain)
    assert "open skill-deltas owed to this agent: 0" in out


# --------------------------------------------------------------------------- #
# idempotence                                                                 #
# --------------------------------------------------------------------------- #


def test_build_is_idempotent(brain):
    agent = "specialist-a"
    _write_journal(brain, agent, [
        _entry(n=1, review="REFUTE by r; residuals: open"),
        _entry(n=2, graduation="ready"),
    ])
    a = st.build_status(agent, brain)
    b = st.build_status(agent, brain)
    assert a == b


def test_write_is_idempotent_and_matches_build(brain):
    agent = "specialist-a"
    _write_journal(brain, agent, [_entry(n=1, graduation="ready")])
    p1 = st.write_status(agent, brain)
    first = open(p1, encoding="utf-8").read()
    p2 = st.write_status(agent, brain)
    second = open(p2, encoding="utf-8").read()
    assert p1 == p2
    assert first == second                       # rebuilding yields the same file
    assert first == st.build_status(agent, brain)  # the writer writes what build returns


# --------------------------------------------------------------------------- #
# no journal / malformed block — clean, never a crash                         #
# --------------------------------------------------------------------------- #


def test_no_journal_yields_clean_status(brain):
    """An agent with no journal yet -> a clean 'no history' status, no crash."""
    out = st.build_status("brand-new-agent", brain)
    assert "no journal history yet" in out.lower()
    # counts still render (from the live ledger — here empty)
    assert "ready to graduate" in out
    assert "open skill-deltas owed to this agent: 0" in out


def test_no_journal_writes_a_file(brain):
    p = st.write_status("brand-new-agent", brain)
    assert os.path.isfile(p)
    assert p.endswith(os.path.join("brand-new-agent", "status.md"))


def test_malformed_block_is_skipped_not_fatal(brain):
    """A garbage block in the journal must not break the boot read — it is
    skipped; the good entries still roll up."""
    agent = "specialist-a"
    path = st.agent_journal_path(agent, brain)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    good = render_journal_entry(_entry(n=1, worked_on=["the real thing"]))
    # a second block that starts like an entry header but is malformed
    garbage = "### J-not a valid header at all\n- nonsense\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(good + "\n" + garbage)
    out = st.build_status(agent, brain)  # must not raise
    assert "the real thing" in out


def test_empty_journal_file_is_clean(brain):
    """A present-but-empty journal file is treated as no history, not a crash."""
    agent = "specialist-a"
    path = st.agent_journal_path(agent, brain)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").close()
    out = st.build_status(agent, brain)
    assert "no journal history yet" in out.lower()


# --------------------------------------------------------------------------- #
# confinement                                                                 #
# --------------------------------------------------------------------------- #


def test_status_path_is_under_wiki_root(brain):
    p = st.agent_status_path("specialist-a", brain)
    assert os.path.realpath(p).startswith(os.path.realpath(st.wiki_root(brain)) + os.sep)
