"""Tests for the cloud-ingest PROCESSING lane (``ingest.cloud``).

The cloud lane is the PROCESSING half of the cloud routine: the Claude
Gmail/Drive/Calendar connectors do the auth+pull (runtime, NOT tested here — it
has no OAuth/network code by design); this code consumes their output as
**normalized connector records** and runs it through the shared spine + genesis
to operator-gated proposals.

So everything here runs against NORMALIZED-RECORD FIXTURES (plain dicts in the
``schema`` shape) — never a live account. The headline acceptances the goal-loop
asks for:

  * the Gmail SENT-folder filter admits inbound ONLY from correspondents derived
    from the founder's SENT mail (spam/cold inbound dropped), reusing the ONE
    canonical allowlist (proven to agree with the mbox ``EmailAdapter``);
  * a secret/credential/PII-bearing record is dropped by the spine (never an
    Event, never in the proposals file);
  * Drive + Calendar records become Events (no spam gate);
  * an empty / fully-filtered dump is a clean no-op (no proposals file written);
  * everything the entrypoint writes is ``status="proposed"`` — nothing applied.
"""

from __future__ import annotations

import json
import mailbox
from pathlib import Path

from ingest.adapters.email_source import EmailAdapter, harvest_sent_correspondents
from ingest.allowlist import build_allowlist
from ingest.cloud import (
    CalendarEvent,
    DriveDoc,
    GmailMessage,
    normalized_records_from_json,
    normalized_records_from_obj,
    process_cloud_records,
)
from ingest.cloud import refresh as refresh_mod
from ingest.cloud.schema import KIND_CALENDAR, KIND_DRIVE, KIND_GMAIL

ME = "founder@co.example"


# --------------------------------------------------------------------------- #
# Fixtures — normalized connector dumps (the shape the connectors map INTO)    #
# --------------------------------------------------------------------------- #


def _dump(*, with_secret: bool = False, with_drive: bool = True, with_cal: bool = True) -> dict:
    """A representative connector dump: one SENT mail (seeds the allowlist),
    one inbound from that correspondent (admitted), one inbound from a stranger
    (spam — dropped), optionally a secret-bearing inbound (dropped by sanitize),
    plus a Drive doc and a Calendar event."""
    gmail = [
        {
            "message_id": "sent-1",
            "sender": ME,
            "to": ["alice@partner.example"],
            "cc": ["bob@partner.example"],
            "date": "2026-06-18T09:00:00Z",
            "body": "Kicking off the pricing thread for the launch deal.",
            "subject": "Pricing for launch",
            "is_sent": True,
        },
        {
            "message_id": "in-1",
            "sender": "alice@partner.example",
            "to": [ME],
            "date": "2026-06-19T10:00:00Z",
            "body": "Works for us — list price 5200 for the launch.",
            "subject": "Re: Pricing for launch",
            "is_sent": False,
        },
        {
            "message_id": "spam-1",
            "sender": "promos@randovendor.example",
            "to": [ME],
            "date": "2026-06-19T11:00:00Z",
            "body": "FLASH SALE — 50% off everything this week!",
            "subject": "Don't miss out",
            "is_sent": False,
        },
    ]
    if with_secret:
        gmail.append(
            {
                "message_id": "leak-1",
                "sender": "alice@partner.example",   # a real correspondent...
                "to": [ME],
                "date": "2026-06-20T08:00:00Z",
                "body": "demo key: sk-ABCDEFGHIJKLMNOP01234 — don't share",  # ...but a secret  # pragma: allowlist secret
                "subject": "key",
                "is_sent": False,
            }
        )
    out: dict = {"gmail": gmail}
    if with_drive:
        out["drive"] = [
            {
                "id": "doc-1",
                "title": "Launch roadmap",
                "content": "Product launch plan targeting the beachhead segment.",
                "modified": "2026-06-17T08:00:00Z",
            }
        ]
    if with_cal:
        out["calendar"] = [
            {
                "id": "evt-1",
                "title": "Pricing review",
                "attendees": [ME, "alice@partner.example"],
                "start": "2026-06-21T08:00:00Z",
                "end": "2026-06-21T09:00:00Z",
            }
        ]
    return out


# --------------------------------------------------------------------------- #
# schema — parsing + mapping to the spine's raw-record shape                   #
# --------------------------------------------------------------------------- #


def test_schema_parses_all_three_lanes_with_aliases():
    recs = normalized_records_from_obj(_dump())
    assert len(recs.gmail) == 3
    assert len(recs.drive) == 1
    assert len(recs.calendar) == 1
    # alias tolerance: "from"/"id"/"name"/"summary"/"event_id" should also parse.
    aliased = normalized_records_from_obj(
        {
            "email": [{"id": "x", "from": "A@B.com", "to": "c@d.com", "is_sent": False, "body": "hi"}],
            "files": [{"file_id": "f1", "name": "Doc", "text": "body"}],
            "events": [{"event_id": "e1", "summary": "Sync", "attendees": "p@q.com"}],
        }
    )
    assert aliased.gmail[0].message_id == "x"
    assert aliased.gmail[0].sender == "a@b.com"        # lowercased
    assert aliased.drive[0].id == "f1" and aliased.drive[0].title == "Doc"
    assert aliased.calendar[0].id == "e1" and aliased.calendar[0].attendees == ("p@q.com",)


def test_missing_lane_is_empty_not_an_error():
    recs = normalized_records_from_obj({"gmail": []})
    assert recs.drive == [] and recs.calendar == []
    assert recs.is_empty()


def test_gmail_to_raw_record_folds_subject_and_body_and_lists_participants():
    m = GmailMessage.from_obj(
        {"message_id": "m", "sender": "A@x.com", "to": ["b@x.com"], "cc": ["c@x.com"],
         "subject": "Hello", "body": "the body", "is_sent": False}
    )
    rec = m.to_raw_record()
    assert rec["kind"] == KIND_GMAIL and rec["source_id"] == "m"
    assert rec["text"] == "Hello\nthe body"
    assert rec["participants"] == ["a@x.com", "b@x.com", "c@x.com"]


def test_drive_and_calendar_to_raw_record_shape():
    d = DriveDoc.from_obj({"id": "d", "title": "T", "content": "C", "modified": "2026-01-01T00:00:00Z"})
    assert d.to_raw_record()["kind"] == KIND_DRIVE
    assert d.to_raw_record()["text"] == "T\nC"
    e = CalendarEvent.from_obj({"id": "e", "title": "Mtg", "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T01:00:00Z"})
    rec = e.to_raw_record()
    assert rec["kind"] == KIND_CALENDAR and rec["observed_at"] == "2026-01-01T00:00:00Z"
    assert "Mtg" in rec["text"]


def test_comma_separated_to_string_splits_into_addresses():
    m = GmailMessage.from_obj({"message_id": "m", "to": "a@x.com, b@y.com; c@z.com", "is_sent": True, "body": "x"})
    assert m.to == ("a@x.com", "b@y.com", "c@z.com")


# --------------------------------------------------------------------------- #
# The SENT-folder filter — reuse of the ONE canonical allowlist                #
# --------------------------------------------------------------------------- #


def test_correspondents_harvested_from_sent_only_excluding_self():
    recs = normalized_records_from_obj(_dump())
    from ingest.cloud.process import correspondents_from_gmail

    corr = correspondents_from_gmail(recs.gmail, user_email=ME)
    # To + Cc of the SENT message; the founder excluded; inbound senders NOT here.
    assert corr == {"alice@partner.example", "bob@partner.example"}
    assert ME not in corr
    assert "promos@randovendor.example" not in corr


def test_inbound_admitted_only_from_correspondents():
    recs = normalized_records_from_obj(_dump())
    res = process_cloud_records(recs, user_email=ME)
    assert res.gmail_sent == 1
    assert res.gmail_inbound == 2           # alice + spam
    assert res.gmail_admitted == 1          # only alice (a correspondent)
    # spam never became an event
    texts = " ".join(e.text for e in res.corpus.all_events()).lower()
    assert "flash sale" not in texts
    assert "list price 5200" in texts


def test_cloud_and_mbox_agree_on_correspondents_no_fork():
    """The anti-fork guarantee: the cloud Gmail lane and the mbox EmailAdapter,
    given the SAME sent message, derive the SAME correspondent set — because both
    route through ``harvest_sent_correspondents`` + ``build_allowlist`` (one
    rule, one allowlist), never a duplicated copy."""
    dump = _dump()
    sent = dump["gmail"][0]

    # cloud side
    from ingest.cloud.process import correspondents_from_gmail

    cloud_corr = correspondents_from_gmail([GmailMessage.from_obj(m) for m in dump["gmail"]], user_email=ME)

    # mbox side — same sent message expressed as an .eml in a Sent-labelled mbox
    mbox_corr = harvest_sent_correspondents(
        [[*sent["to"], *sent["cc"]]], user_email=ME
    )
    assert cloud_corr == mbox_corr == {"alice@partner.example", "bob@partner.example"}

    # and the canonical allowlist built from each is identical
    cloud_tokens = build_allowlist(sorted(cloud_corr), contacts={}).tokens
    mbox_tokens = build_allowlist(sorted(mbox_corr), contacts={}).tokens
    assert cloud_tokens == mbox_tokens


def test_email_adapter_still_uses_shared_helper(tmp_path: Path):
    """The refactor kept the mbox adapter correct: a Sent-labelled message's
    To/Cc become its correspondents via the shared helper."""
    box_path = tmp_path / "mail.mbox"
    box = mailbox.mbox(str(box_path))
    sent = mailbox.mboxMessage()
    sent["X-Gmail-Labels"] = "Sent"
    sent["From"] = ME
    sent["To"] = "alice@partner.example"
    sent["Subject"] = "hi"
    sent.set_payload("hello there")
    box.add(sent)
    box.flush()
    box.close()

    adapter = EmailAdapter(path=str(box_path), user_email=ME)
    adapter.build()
    assert "alice@partner.example" in adapter.sent_correspondents
    assert ME not in adapter.sent_correspondents


def test_empty_allowlist_admits_no_inbound():
    """No sent mail → no correspondents → fail-closed: inbound admits nobody."""
    recs = normalized_records_from_obj(
        {"gmail": [
            {"message_id": "in", "sender": "x@y.com", "to": [ME], "body": "hi", "is_sent": False},
        ]}
    )
    res = process_cloud_records(recs, user_email=ME)
    assert res.allowlist_summary["token_total"] == 0
    assert res.gmail_admitted == 0


# --------------------------------------------------------------------------- #
# Sanitize — secrets/PII dropped by the shared spine                          #
# --------------------------------------------------------------------------- #


def test_secret_bearing_inbound_is_dropped_even_from_a_correspondent():
    recs = normalized_records_from_obj(_dump(with_secret=True))
    res = process_cloud_records(recs, user_email=ME)
    # the leak came from alice (a correspondent) so it was ADMITTED by the
    # allowlist, but the spine's privacy gate drops it -> never an Event.
    assert res.gmail_admitted == 2          # alice's clean reply + her leak
    assert res.dropped_private >= 1
    blob = " ".join(e.text for e in res.corpus.all_events())
    assert "sk-ABCDEFGHIJKLMNOP" not in blob  # pragma: allowlist secret


def test_drive_and_calendar_become_events():
    recs = normalized_records_from_obj(_dump())
    res = process_cloud_records(recs, user_email=ME)
    kinds = {e.kind for e in res.corpus.all_events()}
    assert KIND_DRIVE in kinds and KIND_CALENDAR in kinds
    assert res.drive_seen == 1 and res.calendar_seen == 1


# --------------------------------------------------------------------------- #
# refresh entrypoint — proposals-only, idle no-op, file write, dry-run, stdin  #
# --------------------------------------------------------------------------- #


def test_refresh_writes_proposals_file_all_proposed(tmp_path: Path):
    recs = normalized_records_from_obj(_dump(with_secret=True))
    out = tmp_path / "proposals.json"
    summary = refresh_mod.run_refresh(recs, user_email=ME, out_path=out, today="2026-06-22")

    assert summary["written"] is True
    assert out.is_file()
    data = json.loads(out.read_text())
    assert data["status"] == "proposed" and data["applied"] is False
    assert data["proposals"], "expected at least one proposal"
    assert all(p["status"] == "proposed" for p in data["proposals"])
    # the run accounting is present + non-sensitive
    assert data["run"]["gmail_admitted"] == 2
    assert data["run"]["dropped_private"] >= 1
    # NO secret anywhere in the written file (defense in depth)
    assert "sk-ABCDEFGHIJKLMNOP" not in out.read_text()  # pragma: allowlist secret
    # NO spam-sender body in the file
    assert "FLASH SALE" not in out.read_text()


def test_refresh_idle_dump_is_clean_no_op(tmp_path: Path):
    out = tmp_path / "proposals.json"
    summary = refresh_mod.run_refresh(normalized_records_from_obj({}), out_path=out, today="2026-06-22")
    assert summary["written"] is False
    assert summary["reason"] == "idle_no_records"
    assert not out.exists()                 # idle writes NOTHING


def test_refresh_all_filtered_dump_writes_nothing(tmp_path: Path):
    """A dump whose every record is dropped (only spam inbound, no sent mail)
    produces no events → no proposals → no file."""
    recs = normalized_records_from_obj(
        {"gmail": [{"message_id": "s", "sender": "x@y.com", "to": [ME], "body": "spam", "is_sent": False}]}
    )
    out = tmp_path / "proposals.json"
    summary = refresh_mod.run_refresh(recs, user_email=ME, out_path=out, today="2026-06-22")
    assert summary["written"] is False
    assert summary["reason"] == "no_events_after_filter"
    assert not out.exists()


def test_refresh_dry_run_writes_nothing_but_builds_packet(tmp_path: Path):
    recs = normalized_records_from_obj(_dump())
    out = tmp_path / "proposals.json"
    summary = refresh_mod.run_refresh(recs, user_email=ME, out_path=out, dry_run=True, today="2026-06-22")
    assert summary["written"] is False
    assert summary["reason"] == "dry_run"
    assert summary["packet"] is not None      # processing + genesis still ran
    assert not out.exists()


def test_refresh_does_not_write_genesis_drafts_by_default(tmp_path: Path):
    """A proposals-only cloud run must not mutate genesis/out as a side effect."""
    import genesis_pipeline

    before = set(Path(genesis_pipeline.OUT_DIR).glob("pillar_*.md")) if Path(genesis_pipeline.OUT_DIR).is_dir() else set()
    recs = normalized_records_from_obj(_dump())
    refresh_mod.run_refresh(recs, user_email=ME, out_path=tmp_path / "p.json", today="2026-06-22")
    after = set(Path(genesis_pipeline.OUT_DIR).glob("pillar_*.md")) if Path(genesis_pipeline.OUT_DIR).is_dir() else set()
    assert before == after, "cloud refresh must not write genesis pillar drafts by default"


def test_refresh_main_reads_records_file_and_writes(tmp_path: Path, capsys):
    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()))
    out = tmp_path / "proposals.json"
    rc = refresh_mod.main(["--records", str(dump_path), "--user-email", ME, "--out", str(out)])
    assert rc == 0
    assert out.is_file()
    report = capsys.readouterr().out
    assert "proposals-only" in report
    assert "no OAuth" in report


def test_refresh_main_reads_stdin(tmp_path, monkeypatch, capsys):
    import io

    out = tmp_path / "proposals.json"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_dump())))
    rc = refresh_mod.main(["--user-email", ME, "--out", str(out)])
    assert rc == 0
    assert out.is_file()


def test_normalized_records_from_json_roundtrip():
    recs = normalized_records_from_json(json.dumps(_dump()))
    assert len(recs.gmail) == 3 and len(recs.drive) == 1 and len(recs.calendar) == 1
