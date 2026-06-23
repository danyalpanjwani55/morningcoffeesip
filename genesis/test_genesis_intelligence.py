"""Deterministic harness for the genesis intelligence layer (BUILD-SPEC-02 §6).

Tests the CONTRACTS + RAILS (not LLM taste), with a stub ``LLM`` returning
canned JSON. Run: ``pytest -q`` in this directory.

The 7 required cases:
  1. anchor-required: a zero-anchor proposal is dropped.
  2. proposals-only: every packet artifact is status=="proposed"; the pipeline
     writes nothing outside genesis/out/.
  3. base-roster never re-proposed.
  4. MIN_EVIDENCE: <3 anchored signals -> no agent proposal; >=3 -> one.
  5. egress rail: a secret/PII string raises PrivateDataEgressError; clean passes.
  6. two-doc: summary_md opens "In plain terms"; no raw source_id codes in the
     operator prose (anchors live in a separate evidence block).
  7. full-corpus: since="inception" yields all events; a date yields only newer.
"""

from __future__ import annotations

import json
import os

import pytest

import genesis_pipeline
from genesis_contracts import (
    Anchor,
    EgressGate,
    Event,
    InMemoryCorpus,
    PillarState,
    PrivateDataEgressError,
    new_proposal,
)
from genesis_pipeline import OUT_DIR, run_genesis
from meta_initiative_deriver import derive_meta_initiatives
from review_surface import build_review_packet
from roster_proposer import MIN_EVIDENCE, propose_roster


# --------------------------------------------------------------------------- #
# Stub LLM — canned JSON keyed on prompt content (deterministic, no network).  #
# --------------------------------------------------------------------------- #


class StubLLM:
    """Returns canned JSON. The constructor lets each test dictate exactly what
    the 'model' returns for the MI prompt and the roster prompt."""

    def __init__(self, *, mi_json: str = "[]", roster_json: str = "[]"):
        self._mi = mi_json
        self._roster = roster_json
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        self.calls.append((system, user))
        if "PILLAR:" in user:
            return self._mi
        if "CORPUS EVIDENCE" in user:
            return self._roster
        return "[]"


def _events() -> list[Event]:
    return [
        Event("e1", "2026-06-18T09:00:00Z", "decision",
              "launch_date = 2026-10-15", "standup", "L1",
              meta={"asserted_by": "operator"}),
        Event("e2", "2026-06-10T12:00:00Z", "web",
              "launch_date = 2026-09-01", "partner-blog", "p3"),
        Event("e3", "2026-06-19T08:00:00Z", "email",
              "list_price = 4900", "pricing-thread", "msg7"),
        Event("e4", "2026-06-21T08:00:00Z", "meeting",
              "list_price = 5200", "pricing-review", "L8"),
        Event("e5", "2026-06-20T10:00:00Z", "email",
              "customer onboarding for the first sales deal", "cust-thread", "m2"),
    ]


def _corpus() -> InMemoryCorpus:
    return InMemoryCorpus(_events())


def _pillar_with_anchors(n: int = 2) -> dict[str, PillarState]:
    anchors = [Anchor(f"src{i}", "email", f"loc{i}") for i in range(n)]
    return {
        "gtm": PillarState(
            name="gtm",
            summary="Pricing and launch decisions.",
            anchors=anchors,
        )
    }


# --------------------------------------------------------------------------- #
# Case 1 — anchor-required: zero-anchor proposal dropped                       #
# --------------------------------------------------------------------------- #


def test_case1_mi_without_anchor_is_dropped():
    pillars = _pillar_with_anchors()
    # MI cites no anchors -> must be dropped.
    stub = StubLLM(mi_json=json.dumps(
        [{"title": "Ungrounded thrust", "rationale": "vibes",
          "confidence": "high", "anchor_ids": []}]
    ))
    out = derive_meta_initiatives(pillars, llm=stub, egress=EgressGate())
    assert out == []


def test_case1_mi_with_anchor_survives():
    pillars = _pillar_with_anchors()
    stub = StubLLM(mi_json=json.dumps(
        [{"title": "Grounded thrust", "rationale": "evidence-backed",
          "confidence": "medium", "anchor_ids": [0]}]
    ))
    out = derive_meta_initiatives(pillars, llm=stub, egress=EgressGate())
    assert len(out) == 1
    assert out[0].is_anchored()
    assert out[0].payload["title"] == "Grounded thrust"


def test_case1_mi_out_of_range_anchor_is_dropped():
    # anchor id 99 doesn't resolve -> no valid anchor -> dropped.
    pillars = _pillar_with_anchors(n=2)
    stub = StubLLM(mi_json=json.dumps(
        [{"title": "Fabricated anchor", "rationale": "x",
          "confidence": "low", "anchor_ids": [99]}]
    ))
    out = derive_meta_initiatives(pillars, llm=stub, egress=EgressGate())
    assert out == []


# --------------------------------------------------------------------------- #
# Case 2 — proposals-only + no writes outside genesis/out/                     #
# --------------------------------------------------------------------------- #


def test_case2_all_proposed_and_no_external_writes(tmp_path, monkeypatch):
    # Redirect OUT_DIR into a temp dir and snapshot the rest of the repo tree.
    fake_out = tmp_path / "out"
    monkeypatch.setattr(genesis_pipeline, "OUT_DIR", str(fake_out))

    repo_dir = os.path.dirname(os.path.abspath(genesis_pipeline.__file__))
    before = _snapshot_tree(repo_dir)

    stub = StubLLM(
        mi_json=json.dumps(
            [{"title": "Focused GTM", "rationale": "r", "confidence": "medium",
              "anchor_ids": [0]}]
        ),
        roster_json=json.dumps(
            [{"slug": "gtm-lead", "domain": "gtm", "rationale": "r",
              "anchor_ids": [0, 1, 2]}]
        ),
    )
    packet = run_genesis(
        _corpus(), roster=["product"], since="inception",
        llm=stub, egress=EgressGate(), write_drafts=True,
    )

    # every artifact proposed
    assert all(p.status == "proposed" for p in packet.proposals)
    assert len(packet.proposals) >= 1

    # nothing changed in the repo tree (writes only under the redirected out/)
    after = _snapshot_tree(repo_dir)
    assert after == before, "pipeline wrote outside OUT_DIR"

    # drafts DID land under the redirected out dir
    assert fake_out.exists()
    written = list(fake_out.glob("pillar_*.md"))
    assert written, "expected pillar drafts under OUT_DIR"


def _snapshot_tree(root: str) -> set[str]:
    """Set of (relpath, size) for every file under root, excluding the out/ and
    __pycache__ dirs (those are allowed to change)."""
    snap = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {"out", "__pycache__"}]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            snap.add((os.path.relpath(full, root), size))
    return snap


def test_case2_write_guard_rejects_escape(tmp_path):
    # The path guard refuses any write outside OUT_DIR.
    with pytest.raises(RuntimeError):
        genesis_pipeline._assert_under_out("/tmp/evil.md")


# --------------------------------------------------------------------------- #
# Case 3 — base-roster never re-proposed                                       #
# --------------------------------------------------------------------------- #


def test_case3_base_roster_not_reproposed():
    people = PillarState(name="people")
    # stub proposes 'product' (in base roster) AND 'gtm-lead' (new) — only the
    # new one may survive.
    stub = StubLLM(roster_json=json.dumps([
        {"slug": "product", "domain": "product", "rationale": "x",
         "anchor_ids": [0, 1, 2]},
        {"slug": "gtm-lead", "domain": "gtm", "rationale": "y",
         "anchor_ids": [0, 1, 2]},
    ]))
    out = propose_roster(
        _corpus(), people, base_roster=["product", "ops"],
        llm=stub, egress=EgressGate(),
    )
    slugs = {p.payload["slug"] for p in out}
    assert "product" not in slugs
    assert "gtm-lead" in slugs


def test_case3_base_roster_match_is_case_insensitive():
    people = PillarState(name="people")
    stub = StubLLM(roster_json=json.dumps([
        {"slug": "Product", "domain": "product", "rationale": "x",
         "anchor_ids": [0, 1, 2]},
    ]))
    out = propose_roster(
        _corpus(), people, base_roster=["product"],
        llm=stub, egress=EgressGate(),
    )
    assert out == []


# --------------------------------------------------------------------------- #
# Case 4 — MIN_EVIDENCE floor                                                  #
# --------------------------------------------------------------------------- #


def test_case4_below_min_evidence_no_proposal():
    people = PillarState(name="people")
    assert MIN_EVIDENCE == 3
    # only 2 distinct anchored signals -> no proposal
    stub = StubLLM(roster_json=json.dumps([
        {"slug": "thin", "domain": "thin", "rationale": "x",
         "anchor_ids": [0, 1]},
    ]))
    out = propose_roster(
        _corpus(), people, base_roster=[], llm=stub, egress=EgressGate(),
    )
    assert out == []


def test_case4_at_min_evidence_yields_proposal():
    people = PillarState(name="people")
    stub = StubLLM(roster_json=json.dumps([
        {"slug": "thick", "domain": "thick", "rationale": "x",
         "anchor_ids": [0, 1, 2]},
    ]))
    out = propose_roster(
        _corpus(), people, base_roster=[], llm=stub, egress=EgressGate(),
    )
    assert len(out) == 1
    assert out[0].payload["slug"] == "thick"


def test_case4_duplicate_anchor_ids_dont_inflate_signal():
    # 3 ids but all point to the SAME locator -> 1 distinct signal -> dropped.
    people = PillarState(name="people")
    stub = StubLLM(roster_json=json.dumps([
        {"slug": "dupe", "domain": "d", "rationale": "x",
         "anchor_ids": [0, 0, 0]},
    ]))
    out = propose_roster(
        _corpus(), people, base_roster=[], llm=stub, egress=EgressGate(),
    )
    assert out == []


# --------------------------------------------------------------------------- #
# Case 5 — egress rail                                                         #
# --------------------------------------------------------------------------- #


def test_case5_egress_blocks_secret():
    gate = EgressGate()
    with pytest.raises(PrivateDataEgressError):
        gate.guard("here is the api_key = sk-ABCDEF0123456789ABCDEF")  # pragma: allowlist secret


def test_case5_egress_blocks_pii_ssn():
    gate = EgressGate()
    with pytest.raises(PrivateDataEgressError):
        gate.guard("the SSN on file is 123-45-6789 for the contract")  # pragma: allowlist secret


def test_case5_egress_blocks_pem_key():
    gate = EgressGate()
    with pytest.raises(PrivateDataEgressError):
        gate.guard("-----BEGIN RSA PRIVATE KEY-----\nMIIE...")  # pragma: allowlist secret


def test_case5_egress_blocks_empty_unclassifiable():
    gate = EgressGate()
    with pytest.raises(PrivateDataEgressError):
        gate.guard("   ")


def test_case5_egress_passes_clean_spec():
    gate = EgressGate()
    text = "PILLAR: gtm\nSUMMARY: pricing and launch decisions\nEVIDENCE: [0] email"
    assert gate.guard(text) == text
    assert gate.classify(text) == "public"


def test_case5_pipeline_blocks_private_pillar_prose():
    # If a pillar summary smuggles a secret, the deriver's egress.guard must
    # raise rather than ship it to the model.
    pillars = {
        "gtm": PillarState(
            name="gtm",
            summary="our deploy password = hunter2-very-secret-token=abc123",  # pragma: allowlist secret
            anchors=[Anchor("s0", "email", "l0")],
        )
    }
    stub = StubLLM(mi_json="[]")
    with pytest.raises(PrivateDataEgressError):
        derive_meta_initiatives(pillars, llm=stub, egress=EgressGate())


# --------------------------------------------------------------------------- #
# Case 6 — two-document-types                                                  #
# --------------------------------------------------------------------------- #


def test_case6_summary_opens_in_plain_terms():
    pillars = _pillar_with_anchors()
    mi = [new_proposal(
        type="meta_initiative", confidence="medium", rationale="focus on GTM",
        source_anchors=[Anchor("pricing-thread", "email", "msg7")],
        payload={"title": "Focused GTM"},
    )]
    packet = build_review_packet(pillars, mi, [], [])
    assert packet.summary_md.lstrip().startswith("# In plain terms")


def test_case6_no_raw_source_ids_in_operator_prose():
    # source_id codes must NOT appear in the prose sections — only in the
    # fenced "Evidence" block at the bottom.
    pillars = _pillar_with_anchors()
    mi = [new_proposal(
        type="meta_initiative", confidence="medium",
        rationale="pricing recurs",
        source_anchors=[Anchor("SECRET-SOURCE-CODE-XYZ", "email", "LOC-123")],
        payload={"title": "Focused GTM"},
    )]
    roster = [new_proposal(
        type="agent", confidence="medium", rationale="enough signal",
        source_anchors=[Anchor("ROSTER-SRC-ABC", "email", "L9")],
        payload={"slug": "gtm-lead"},
    )]
    packet = build_review_packet(pillars, mi, roster, [])

    prose, _, evidence = packet.summary_md.partition(
        "## Evidence (references behind each proposal)"
    )
    # the raw source_id codes appear ONLY after the evidence header
    assert "SECRET-SOURCE-CODE-XYZ" not in prose
    assert "ROSTER-SRC-ABC" not in prose
    assert "SECRET-SOURCE-CODE-XYZ" in evidence
    assert "ROSTER-SRC-ABC" in evidence


def test_case6_packet_rejects_applied_proposal():
    # The review surface may only carry status='proposed'.
    bad = new_proposal(
        type="meta_initiative", confidence="high", rationale="x",
        source_anchors=[Anchor("s", "email", "l")], payload={"title": "t"},
    )
    object.__setattr__(bad, "status", "applied")  # force a non-proposed status
    with pytest.raises(ValueError):
        build_review_packet({}, [bad], [], [])


# --------------------------------------------------------------------------- #
# Case 7 — full-corpus mode                                                    #
# --------------------------------------------------------------------------- #


def test_case7_inception_yields_all_events():
    corpus = _corpus()
    got = list(corpus.events_since("inception"))
    assert len(got) == len(_events())


def test_case7_date_yields_only_newer():
    corpus = _corpus()
    # e2 is 2026-06-10, e1 2026-06-18, e5 2026-06-20, e3 2026-06-19, e4 2026-06-21
    got = list(corpus.events_since("2026-06-19T00:00:00Z"))
    ids = {e.event_id for e in got}
    # strictly newer than the cutoff: e3(06-19 08:00), e5(06-20), e4(06-21)
    assert ids == {"e3", "e4", "e5"}
    assert "e1" not in ids and "e2" not in ids


def test_case7_pipeline_since_filters_pillars():
    # With a late cutoff, only the newest events feed the pillars.
    stub = StubLLM()
    packet = run_genesis(
        _corpus(), roster=[], since="2026-06-20T12:00:00Z",
        llm=stub, egress=EgressGate(), write_drafts=False,
    )
    # only e4 (06-21, list_price) survives the cutoff -> a gtm pillar exists,
    # and no 'general'/older pillars from e1/e2.
    all_claim_ids = {
        c.claim_id for p in packet.pillars.values() for c in p.claims
    }
    assert all_claim_ids == {"e4"}


# --------------------------------------------------------------------------- #
# End-to-end smoke: a full run returns a valid packet                          #
# --------------------------------------------------------------------------- #


def test_end_to_end_valid_packet(monkeypatch, tmp_path):
    monkeypatch.setattr(genesis_pipeline, "OUT_DIR", str(tmp_path / "out"))
    stub = StubLLM(
        mi_json=json.dumps(
            [{"title": "Win beachhead", "rationale": "pricing+launch recur",
              "confidence": "medium", "anchor_ids": [0]}]
        ),
        roster_json=json.dumps(
            [{"slug": "gtm-lead", "domain": "gtm", "rationale": "recurring deals",
              "anchor_ids": [0, 1, 2]}]
        ),
    )
    packet = run_genesis(
        _corpus(), roster=["product", "ops"], since="inception",
        llm=stub, egress=EgressGate(), write_drafts=True,
    )
    assert packet.summary_md.lstrip().startswith("# In plain terms")
    assert packet.proposals, "expected at least one proposal"
    for p in packet.proposals:
        assert p.status == "proposed"
        assert p.is_anchored()
