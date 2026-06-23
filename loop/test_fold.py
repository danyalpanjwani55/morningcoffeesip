"""Tests for loop/fold — compound pulses into the growing agent wikis.

Deterministic; stub LLM; no network. Brain writes isolated to tmp_path.

Pins the contract (from the live fold skill):
  * pointer-tier pulses append a dated line to <brain>/wiki/<agent>/log.md
    (the wiki GROWS by accretion across runs);
  * fold-tier pulses ALSO get a CITED wiki page built via the REUSED genesis
    agent_wiki_builder, mirrored into the brain-rooted wiki;
  * a fold-tier pulse with NO anchors degrades to pointer (cite >=1);
  * the sweep is chronological (oldest first);
  * a pulse whose source file is already ``folded:`` is SKIPPED (idempotent);
  * restamping is the only mutation to a source pulse;
  * memory persists: a second run appends to the SAME log (growth).

Run: ``/usr/bin/python3 -B -m pytest -q``.
"""

from __future__ import annotations

import json
import os

import pytest

import fold as F
from genesis_contracts import Anchor, EgressGate, Event


# --------------------------------------------------------------------------- #
# Stubs (mirror the genesis agent_wiki_builder test stubs)                     #
# --------------------------------------------------------------------------- #


class StubLLM:
    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        if "synthesize" in system:
            return json.dumps({"summary": "syn", "themes": ["t"]})
        return json.dumps({"what_it_is": "a source doc", "known": ["fact = 1"]})


@pytest.fixture
def brain(tmp_path):
    return str(tmp_path / "brain")


def _corpus():
    return F.corpus_from_events([
        Event("e1", "2026-06-20T08:00:00Z", "email", "pricing thread body",
              "pricing-thread", "msg7"),
    ])


# --------------------------------------------------------------------------- #
# pointer tier — the wiki grows by accretion                                  #
# --------------------------------------------------------------------------- #


def test_pointer_pulse_grows_the_log(brain):
    pulse = F.Pulse(
        pulse_id="p1", date="2026-06-21", agents=("ops-lead",), tier=F.POINTER,
        lesson="checked the merge graph not the narrator log",
    )
    res = F.fold([pulse], brain_root=brain, today="2026-06-22")
    assert res.total_folded == 1
    assert res.agent_log_lines_added["ops-lead"] == 1

    log = F.agent_log_path("ops-lead", brain)
    assert os.path.isfile(log)
    with open(log) as fh:
        body = fh.read()
    assert "checked the merge graph" in body
    assert "src `p1`" in body


def test_log_persists_and_grows_across_runs(brain):
    F.fold([F.Pulse("p1", "2026-06-21", ("ops-lead",), F.POINTER, "lesson one")],
           brain_root=brain, today="2026-06-22")
    F.fold([F.Pulse("p2", "2026-06-23", ("ops-lead",), F.POINTER, "lesson two")],
           brain_root=brain, today="2026-06-24")
    with open(F.agent_log_path("ops-lead", brain)) as fh:
        body = fh.read()
    # BOTH lessons are in the same log — memory accumulated (grew), not replaced
    assert "lesson one" in body and "lesson two" in body
    # exactly one header (the second run appended, didn't re-seed)
    assert body.count("wiki log") == 1


# --------------------------------------------------------------------------- #
# fold tier — a CITED page via the reused builder                             #
# --------------------------------------------------------------------------- #


def test_fold_tier_builds_cited_page(brain):
    pulse = F.Pulse(
        pulse_id="p9", date="2026-06-21", agents=("gtm",), tier=F.FOLD,
        lesson="pricing model is durable domain knowledge",
        anchors=(Anchor("pricing-thread", "email", "msg7"),),
    )
    res = F.fold([pulse], corpus=_corpus(), llm=StubLLM(), egress=EgressGate(),
                 brain_root=brain, today="2026-06-22")
    assert "gtm" in res.wikis_built
    # a cited source page was mirrored into the brain-rooted growing wiki
    src_dir = os.path.join(F.wiki_root(brain), "gtm", "sources")
    assert os.path.isdir(src_dir)
    pages = os.listdir(src_dir)
    assert pages, "expected a cited source page mirrored into the brain wiki"
    with open(os.path.join(src_dir, pages[0])) as fh:
        page = fh.read()
    assert "pricing-thread" in page                  # the page carries its anchor
    assert "DRAFT" in page                            # stamped DRAFT (not ratified)


def test_fold_tier_without_anchors_degrades_to_pointer(brain):
    # no anchors -> cannot cite a page -> pointer only (no wiki built)
    pulse = F.Pulse("p0", "2026-06-21", ("ops-lead",), F.FOLD, "unanchored claim")
    res = F.fold([pulse], brain_root=brain, today="2026-06-22")
    assert res.wikis_built == []                      # nothing built
    assert res.agent_log_lines_added["ops-lead"] == 1      # but a pointer line landed


# --------------------------------------------------------------------------- #
# chronology + idempotency + restamp                                          #
# --------------------------------------------------------------------------- #


def test_sweep_is_chronological_oldest_first(brain):
    out_of_order = [
        F.Pulse("late", "2026-06-25", ("ops-lead",), F.POINTER, "later lesson"),
        F.Pulse("early", "2026-06-20", ("ops-lead",), F.POINTER, "earlier lesson"),
    ]
    res = F.fold(out_of_order, brain_root=brain, today="2026-06-26")
    # folded in chronological (date) order
    assert res.folded_pulse_ids == ["early", "late"]
    with open(F.agent_log_path("ops-lead", brain)) as fh:
        body = fh.read()
    # the earlier lesson appears above the later one in the growing log
    assert body.index("earlier lesson") < body.index("later lesson")


def test_already_folded_pulse_is_skipped(brain, tmp_path):
    # a pulse file already carrying folded: is skipped (idempotent)
    pf = tmp_path / "pulse-folded.md"
    pf.write_text("---\nfolded: [ops-lead] (2026-06-01)\n---\n\nbody\n")
    pulse = F.Pulse("pf", "2026-06-21", ("ops-lead",), F.POINTER, "should be skipped",
                    path=str(pf))
    res = F.fold([pulse], brain_root=brain, today="2026-06-22")
    assert res.skipped_already_folded == ["pf"]
    assert res.folded_pulse_ids == []
    # nothing grew the log
    assert not os.path.isfile(F.agent_log_path("ops-lead", brain))


def test_unfolded_pulse_file_gets_restamped(brain, tmp_path):
    pf = tmp_path / "pulse-fresh.md"
    pf.write_text("---\ntitle: a pulse\n---\n\nbody\n")
    pulse = F.Pulse("pfresh", "2026-06-21", ("ops-lead", "product"), F.POINTER,
                    "a real lesson", path=str(pf))
    res = F.fold([pulse], brain_root=brain, today="2026-06-22")
    assert res.restamped_files == [str(pf)]
    text = pf.read_text()
    assert F.is_folded(text)                          # now stamped
    assert "ops-lead" in text and "product" in text
    # both agents' logs grew
    assert res.agent_log_lines_added == {"ops-lead": 1, "product": 1}


def test_restamp_replaces_pending(brain):
    text = "---\nfolded: pending\ntitle: x\n---\n\nbody\n"
    assert not F.is_folded(text)                      # pending == unfolded
    out = F.restamp_folded(text, ["ops-lead"], "2026-06-22")
    assert F.is_folded(out)
    assert out.count("folded:") == 1                  # replaced, not duplicated


# --------------------------------------------------------------------------- #
# confinement                                                                 #
# --------------------------------------------------------------------------- #


def test_writes_confined_to_wiki_root(brain):
    F.fold([F.Pulse("p1", "2026-06-21", ("ops-lead",), F.POINTER, "x")],
           brain_root=brain, today="2026-06-22")
    log = os.path.realpath(F.agent_log_path("ops-lead", brain))
    assert log.startswith(os.path.realpath(F.wiki_root(brain)))
