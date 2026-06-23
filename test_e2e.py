"""End-to-end smoke test for the on-ramp (run.py) on the bundled sample corpus.

This walks the WHOLE journey a stranger would walk — point at the bundled
``examples/sample-company`` → ingest → genesis → review packet → ratify → build
cited agent wikis → handoff — and asserts the load-bearing invariants:

  * a review packet IS produced (Type-2: opens "In plain terms"; every proposal
    is ``status="proposed"`` and anchored);
  * the bundled sample exercises the privacy gate (>=1 record dropped private)
    and the conflict path (>1 pillar populated);
  * on AUTO-RATIFY, agent wikis ARE produced — cited + 🟡 DRAFT stamped;
  * NOTHING is auto-applied: with ratify="none" no wiki is built; the packet's
    own proposals never flip off "proposed"; and the dropped secret/PII content
    appears NOWHERE in any produced artifact or in the printed output.

All writes are redirected into a tmp dir (the genesis pillar-draft OUT_DIR + the
agent-wiki WIKI_ROOT), so the test leaves no artifacts AND proves writes are
confined to that one root.

Run (per the repo convention):
    rm -rf ~/Library/Caches/com.apple.python 2>/dev/null; \\
    /usr/bin/python3 -B -m pytest -q
"""

from __future__ import annotations

import io
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_GENESIS = os.path.join(_REPO_ROOT, "genesis")
for _p in (_REPO_ROOT, _GENESIS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run                       # noqa: E402  (the on-ramp under test)
import genesis_pipeline          # noqa: E402
import agent_wiki_builder        # noqa: E402


# The fake secret + PII planted in the sample's two DO-NOT-INGEST notes. These
# must never surface in any produced artifact or printed output.
_SECRET_NEEDLES = ("sk-TESTfakekey", "123-45-6789")


@pytest.fixture()
def confined_out(tmp_path, monkeypatch):
    """Redirect every write the journey performs into a tmp dir, and return it.

    Two roots: the pipeline's pillar-draft ``OUT_DIR`` (read as a module global
    at call time) and the wiki builder's ``WIKI_ROOT`` (a module global the
    builder's write-guard reads). Patching both confines the whole journey.
    """
    out = tmp_path / "out"
    wiki = out / "wiki"
    monkeypatch.setattr(genesis_pipeline, "OUT_DIR", str(out))
    monkeypatch.setattr(agent_wiki_builder, "WIKI_ROOT", str(wiki))
    return out


def _walk(auto_ratify, confined_out):
    """Run the journey on the bundled sample and return (result, printed_text)."""
    buf = io.StringIO()
    result = run.run_journey(
        sources_dir=run.SAMPLE_DIR,
        auto_ratify=auto_ratify,
        input_fn=lambda _prompt: "n",   # never reached under auto-ratify
        today="2026-06-23",
        out_stream=buf,
    )
    return result, buf.getvalue()


# --------------------------------------------------------------------------- #
# The sample corpus is present + non-trivial                                   #
# --------------------------------------------------------------------------- #


def test_sample_corpus_exists():
    assert os.path.isdir(run.SAMPLE_DIR), "bundled sample-company corpus missing"
    notes = os.path.join(run.SAMPLE_DIR, "notes")
    mail = os.path.join(run.SAMPLE_DIR, "mail")
    assert os.path.isdir(notes) and os.path.isdir(mail)
    assert any(f.endswith((".md", ".txt")) for f in os.listdir(notes))
    assert any(f.endswith(".eml") for f in os.listdir(mail))


# --------------------------------------------------------------------------- #
# Ingest: privacy gate + dedup behave on the real sample                       #
# --------------------------------------------------------------------------- #


def test_ingest_keeps_clean_and_drops_private(confined_out):
    result, _ = _walk("none", confined_out)
    ingest = result["ingest"]
    # Clean notes/mail flow through; the two planted secret/PII notes are dropped.
    assert ingest.kept >= 5, "expected the clean corpus to ingest"
    assert ingest.dropped_private >= 1, (
        "expected the planted secret/PII notes to be dropped by the privacy gate"
    )


# --------------------------------------------------------------------------- #
# A review packet IS produced, and it is a proper Type-2 gate surface          #
# --------------------------------------------------------------------------- #


def test_review_packet_is_produced_and_type2(confined_out):
    result, printed = _walk("none", confined_out)
    packet = result["packet"]
    assert packet is not None, "no review packet was produced"

    # Type-2: opens "In plain terms".
    assert packet.summary_md.lstrip().startswith("# In plain terms")
    # The packet got printed in the REVIEW step.
    assert "In plain terms" in printed

    # >1 pillar populated (the sample spans gtm + operations at least).
    assert len(packet.pillars) >= 2

    # Every proposal is proposed + anchored (the engine's invariants, surfaced).
    assert packet.proposals, "expected at least one proposal from the sample"
    for p in packet.proposals:
        assert p.status == "proposed"
        assert p.is_anchored()


def test_sample_proposes_a_roster_agent(confined_out):
    """The sample is built so the customer-support signal clears the
    >=3-distinct-anchored-signals floor → exactly the kind of agent the on-ramp
    exists to surface. (If this regresses, the floor or the corpus drifted.)"""
    result, _ = _walk("none", confined_out)
    agents = [p for p in result["packet"].proposals if p.type == "agent"]
    assert agents, "expected >=1 agent proposal from the sample corpus"
    slugs = {p.payload.get("slug") for p in agents}
    assert "customer-support" in slugs


# --------------------------------------------------------------------------- #
# AUTO-RATIFY: cited agent wikis ARE produced (and only then)                   #
# --------------------------------------------------------------------------- #


def test_auto_ratify_builds_cited_draft_wikis(confined_out):
    result, _ = _walk("all", confined_out)

    ratified = result["ratified"]
    wikis = result["wikis"]
    assert ratified, "auto-ratify should have ratified >=1 agent"
    assert wikis, "auto-ratify should have BUILT >=1 agent wiki"

    for r in wikis:
        # index + log + >=1 cited source page + a concept page, all under the
        # confined wiki root.
        assert os.path.isfile(r.index_path)
        assert os.path.isfile(r.log_path)
        assert r.source_pages, "a built wiki must have >=1 cited source page"
        assert r.concept_pages, "a built wiki must have a concept page"

        wiki_root = str(confined_out / "wiki")
        for path in [r.index_path, r.log_path, *r.source_pages, *r.concept_pages]:
            assert os.path.realpath(path).startswith(os.path.realpath(wiki_root)), (
                f"wiki write escaped the confined root: {path}"
            )

        # Pages are CITED + DRAFT-stamped (verify-before-relay + proposals-only).
        for src in r.source_pages:
            text = _read(src)
            assert "🟡 DRAFT" in text
            assert "## Source anchors (citations)" in text
            assert "`" in text  # carries at least one backticked anchor ref
        assert "🟡 DRAFT" in _read(r.index_path)


# --------------------------------------------------------------------------- #
# NOTHING is auto-applied                                                       #
# --------------------------------------------------------------------------- #


def test_no_ratify_builds_no_wiki(confined_out):
    result, printed = _walk("none", confined_out)
    assert result["ratified"] == []
    assert result["wikis"] == []
    # No wiki tree was created at all.
    wiki_root = confined_out / "wiki"
    if wiki_root.exists():
        assert not any(wiki_root.rglob("*.md")), "a wiki was built without ratify"
    assert "no wiki built" in printed


def test_packet_proposals_stay_proposed_even_after_auto_ratify(confined_out):
    """Ratifying produces ratified COPIES; the packet's own proposals must never
    flip off 'proposed' (the review surface is a gate, never an apply surface)."""
    result, _ = _walk("all", confined_out)
    for p in result["packet"].proposals:
        assert p.status == "proposed"
    # ...and the things actually ratified are a separate, 'ratified'-status set.
    for p in result["ratified"]:
        assert p.status == "ratified"


def test_secret_and_pii_never_leak_anywhere(confined_out):
    """The dropped secret/PII must appear in NO produced artifact and NOT in the
    printed journey output — the strongest 'nothing private escaped' check."""
    result, printed = _walk("all", confined_out)

    for needle in _SECRET_NEEDLES:
        assert needle not in printed, f"{needle!r} leaked into printed output"

    # Sweep every file the confined run wrote.
    for dirpath, _dirs, files in os.walk(confined_out):
        for fn in files:
            text = _read(os.path.join(dirpath, fn))
            for needle in _SECRET_NEEDLES:
                assert needle not in text, (
                    f"{needle!r} leaked into a produced artifact: "
                    f"{os.path.join(dirpath, fn)}"
                )


def test_journey_writes_only_under_confined_out(confined_out):
    """Defense in depth: snapshot the repo tree (minus out/ + caches) before and
    after a full auto-ratify run; nothing outside the confined out/ may change."""
    before = _snapshot_repo()
    _walk("all", confined_out)
    after = _snapshot_repo()
    assert after == before, "the journey wrote outside the confined OUT_DIR"


# --------------------------------------------------------------------------- #
# The CLI entry point is wired (smoke)                                          #
# --------------------------------------------------------------------------- #


def test_cli_main_runs_on_sample(confined_out, monkeypatch, capsys):
    """`run.main([...])` exits 0 on the bundled sample with --auto-ratify=none."""
    rc = run.main(["--sources", run.SAMPLE_DIR, "--auto-ratify=none"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "In plain terms" in out
    assert "HANDOFF" in out


def test_cli_main_rejects_missing_sources(tmp_path):
    missing = str(tmp_path / "nope")
    rc = run.main(["--sources", missing])
    assert rc == 2


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _snapshot_repo() -> set:
    """(relpath, size) for every file under the repo, excluding volatile dirs
    (the real genesis/out, caches, .git, .pytest_cache)."""
    snap = set()
    skip_dirs = {"out", "__pycache__", ".git", ".pytest_cache"}
    for dirpath, dirnames, filenames in os.walk(_REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            snap.add((os.path.relpath(full, _REPO_ROOT), size))
    return snap
