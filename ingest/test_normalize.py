"""Tests for ingest.normalize — coerce a raw record into a genesis Event."""

from __future__ import annotations

from genesis_contracts import Event
from ingest.normalize import RawRecord, normalize_record


def test_produces_a_genesis_event():
    ev = normalize_record(
        {
            "kind": "email",
            "text": "list_price = 5200",
            "source_id": "pricing-thread",
            "locator": "msg7",
            "observed_at": "2026-06-20T10:00:00Z",
            "asserted_by": "operator",
        }
    )
    assert isinstance(ev, Event)
    assert ev.kind == "email"
    assert ev.source_id == "pricing-thread"
    assert ev.locator == "msg7"
    assert ev.text == "list_price = 5200"
    # meta carries the keys the genesis pipeline reads
    assert ev.meta["asserted_by"] == "operator"


def test_timestamp_normalized_to_utc_z():
    # an offset time -> normalized to ...Z UTC
    ev = normalize_record({"text": "x", "source_id": "s", "occurred_at": "2026-06-20T12:00:00+02:00"})
    assert ev.observed_at == "2026-06-20T10:00:00Z"


def test_missing_timestamp_falls_back_to_ingested_at():
    ev = normalize_record({"text": "x", "source_id": "s"}, ingested_at="2026-01-01T00:00:00Z")
    assert ev.observed_at == "2026-01-01T00:00:00Z"


def test_rfc2822_date_is_parsed():
    ev = normalize_record({"text": "x", "source_id": "s", "date": "Sat, 20 Jun 2026 10:00:00 +0000"})
    assert ev.observed_at == "2026-06-20T10:00:00Z"


def test_participants_coerced_to_string_tuple():
    ev = normalize_record(
        {
            "text": "x",
            "source_id": "s",
            "participants": [{"email": "A@X.com"}, "Bob", {"name": "Cara"}],
        }
    )
    assert ev.participants == ("a@x.com", "Bob", "Cara")


def test_event_id_prefers_explicit_then_source_locator():
    explicit = normalize_record({"text": "x", "source_id": "s", "locator": "L1", "event_id": "EID"})
    assert explicit.event_id == "EID"
    derived = normalize_record({"text": "x", "source_id": "s", "locator": "L1"})
    assert derived.event_id == "s:L1"
    no_loc = normalize_record({"text": "x", "source_id": "s"})
    assert no_loc.event_id == "s"


def test_rawrecord_input_works():
    rr = RawRecord(kind="note", text="hello", source_id="n1", meta={"owner": "founder"})
    ev = normalize_record(rr, ingested_at="2026-01-01T00:00:00Z")
    assert ev.kind == "note"
    assert ev.meta["owner"] == "founder"


def test_fact_line_survives_into_event_text():
    # The genesis pipeline parses a leading "key = value" line; normalize must
    # preserve that line verbatim in the event text.
    ev = normalize_record({"text": "launch_date = 2026-10-15\nmore context", "source_id": "s"})
    assert ev.text.splitlines()[0] == "launch_date = 2026-10-15"
