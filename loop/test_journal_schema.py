"""Tests for loop/journal_schema — the round-trip contract of the learning-loop
v2 shapes (concept STATE file, journal entry, router index).

The contract: ``parse(render(x)) == x`` for every shape; a malformed block
raises ``SchemaError`` (never a silent partial parse).
"""

from __future__ import annotations

import pytest

from journal_schema import (
    SchemaError,
    ConceptState, render_concept_state, parse_concept_state,
    JournalEntry, render_journal_entry, parse_journal_entry,
    RouterRow, render_router_index, parse_router_index,
)


# --- Concept STATE file ----------------------------------------------------

def test_concept_state_round_trips_full():
    cs = ConceptState(
        slug="customer-support",
        agent="specialist-support",
        state_updated="2026-06-29",
        recurrent_state=[
            "refund SLA is 48h · high · sources/02#L4",
            "tier-1 handles billing · medium · sources/03#L9",
        ],
        history=["2026-06-20 was 72h → changed to 48h because sources/02#L4"],
        overview="Support owns inbound tickets, refunds, and the escalation ladder.",
        source_docs=["sources/02 the refund policy · refund#L4", "sources/03 the tier map"],
    )
    back = parse_concept_state(render_concept_state(cs))
    assert back == cs


def test_concept_state_round_trips_empty_sections():
    cs = ConceptState(slug="new-thing", agent="a", state_updated="2026-06-29")
    back = parse_concept_state(render_concept_state(cs))
    assert back == cs
    assert back.recurrent_state == [] and back.source_docs == [] and back.overview == ""


def test_concept_state_history_kept_separate_from_state():
    cs = ConceptState(
        slug="c", agent="a", state_updated="d",
        recurrent_state=["x is true · high · src#1"],
        history=["2026-01-01 was y → changed to x because src#1"],
    )
    back = parse_concept_state(render_concept_state(cs))
    assert back.recurrent_state == ["x is true · high · src#1"]
    assert back.history == ["2026-01-01 was y → changed to x because src#1"]


def test_concept_state_missing_frontmatter_raises():
    with pytest.raises(SchemaError):
        parse_concept_state("# c\n\n## RECURRENT STATE\n- x\n")


# --- Journal entry ---------------------------------------------------------

def test_journal_entry_round_trips_full():
    je = JournalEntry(
        agent="specialist-support", n=7, date="2026-06-29", title="refund SLA drift",
        worked_on=["read the refund policy", "answered 3 tickets"],
        understood=["the SLA changed in May"],
        lesson="I quoted 72h from memory; the policy says 48h",
        symptom="quoting an SLA without opening the policy concept",
        proposed_delta="support skill: always open customer-support concept before quoting an SLA",
        review="CONCUR by support-twin; residuals: none",
        next_time="open the owning concept + its source doc before quoting any policy number",
        concept_touched=["customer-support", "billing"],
        graduation="ready",
    )
    back = parse_journal_entry(render_journal_entry(je))
    assert back == je


def test_journal_entry_round_trips_minimal_none_fields():
    je = JournalEntry(agent="a", n=1, date="2026-06-29", title="quiet day",
                      next_time="keep going")
    back = parse_journal_entry(render_journal_entry(je))
    assert back == je
    assert back.lesson == "none" and back.symptom == "n/a" and back.graduation == "none"


def test_journal_entry_id_is_zero_padded_and_parses():
    je = JournalEntry(agent="ava", n=3, date="2026-06-29", title="t", next_time="x")
    text = render_journal_entry(je)
    assert "### J-ava-003 ·" in text
    assert parse_journal_entry(text).n == 3


def test_journal_entry_malformed_header_raises():
    with pytest.raises(SchemaError):
        parse_journal_entry("- worked-on: x\n- next-time: y\n")


# --- Router index ----------------------------------------------------------

def test_router_index_round_trips():
    rows = [
        RouterRow("customer-support", "refund SLA 48h · high",
                  "concepts/customer-support.md §overview", "sources/02, sources/03"),
        RouterRow("billing", "Stripe is the processor · high",
                  "concepts/billing.md §overview", "sources/05"),
    ]
    back = parse_router_index(render_router_index("specialist-support", rows))
    assert back == rows


def test_router_index_empty_is_clean():
    text = render_router_index("a", [])
    assert "_(none" in text
    assert parse_router_index(text) == []


def test_router_index_skips_header_and_divider_rows():
    rows = [RouterRow("c", "s", "concepts/c.md §overview", "sources/01")]
    text = render_router_index("a", rows)
    parsed = parse_router_index(text)
    assert len(parsed) == 1 and parsed[0].concept == "c"
