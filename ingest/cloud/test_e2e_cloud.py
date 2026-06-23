"""END-TO-END test of the CLOUD-lane wiring, on NORMALIZED-RECORD fixtures only.

The cloud twin of ``ingest/local/test_e2e_local.py``. It proves the whole cloud
chain — the connector dump the scheduled routine pulled (Gmail / Drive / Calendar
records) → the Gmail SENT-folder correspondent filter → the ONE canonical
allowlist → the shared sanitize/normalize/dedup spine → genesis Events — wired
exactly as the cloud entrypoint (``ingest.cloud.refresh``) AND the on-ramp
(``run.py``) wire it. It asserts, with explicit checks, the four behaviors the
lane contract turns on:

  (a) the Gmail SENT-folder filter KEEPS an inbound from a correspondent (someone
      a SENT message addressed) and DROPS an inbound from a STRANGER (a sender the
      founder never wrote to) — the de-spam rule, on the cloud Gmail records;
  (b) the allowlist is SEEDED from the SENT correspondents, and it is the SAME
      canonical ``ingest.allowlist.Allowlist`` type the iMessage / WhatsApp lanes
      consume — NOT a second allowlist the cloud lane owns (asserted by identity:
      ``type(...) is Allowlist`` + agreement with ``build_allowlist`` directly);
  (c) a Drive doc and a Calendar event each become a genesis Event (no spam gate —
      they are the founder's own shared docs + their own calendar);
  (d) a planted SECRET is sanitized away by the spine before it can become an
      Event (dropped even when it arrives from a real correspondent).

Why normalized-record fixtures (and honestly so): the auth + pull is the Claude
Gmail / Drive / Calendar **connectors'** job, inside the scheduled routine
(runtime, lane B) — there is NO OAuth / network code in this package by design
(``docs/CLOUD-ROUTINE.md`` "the honest status"). The seam between the pull and the
processing is the **normalized connector record** (``ingest.cloud.schema``): the
shape the routine maps connector output INTO, and the only thing this code reads.
So every fixture here is a plain dict in that shape — never a live account — which
makes the PROCESSING code-testable while the connector pull stays an agent/runtime
concern.

GATED-FOR-OPERATOR (documented, not done here): attaching the live read-only
Gmail/Drive/Calendar connectors to a scheduled routine and letting it pull is an
operator action (``docs/CLOUD-ROUTINE.md`` + ``examples/cloud-routine/``). This
suite proves the code against record fixtures matching the connector output; the
live pull + the scheduled run are the operator's to enable.

Run (the repo convention — bust the Apple bytecode cache, not just __pycache__):
    rm -rf ~/Library/Caches/com.apple.python 2>/dev/null; \\
    /usr/bin/python3 -B -m pytest -q
"""

from __future__ import annotations

import io
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# The cloud lane imports flat from ``ingest`` (repo root on sys.path). run.py
# lives at the repo root; put it + genesis/ on the path so the e2e leg that
# drives the on-ramp can import ``run`` the way ``test_e2e.py`` does.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_GENESIS = os.path.join(_REPO_ROOT, "genesis")
for _p in (_REPO_ROOT, _GENESIS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ingest.allowlist import Allowlist, build_allowlist
from ingest.cloud import (
    correspondents_from_gmail,
    normalized_records_from_obj,
    process_cloud_records,
)
from ingest.cloud import refresh as refresh_mod
from ingest.cloud.schema import KIND_CALENDAR, KIND_DRIVE

# --------------------------------------------------------------------------- #
# The cast (no real people): one correspondent the founder has emailed, one    #
# stranger the founder never wrote to. The friend is reachable across lanes —  #
# email (cloud Gmail) and a phone (an iMessage handle) — so we can prove the    #
# ONE allowlist the cloud lane SEEDS also gates the local message lanes.        #
# --------------------------------------------------------------------------- #

FOUNDER = "founder@my-co.example"
FRIEND_EMAIL = "alice@partner.example"
FRIEND_PHONE = "+14155550101"                       # iMessage handle / contact phone
STRANGER_EMAIL = "deals@spam-newsletter.example"
STRANGER_PHONE = "+19998887777"

# A fake secret planted in an inbound FROM the correspondent (so the allowlist
# ADMITS the message and the privacy gate is the only thing that can stop it).
PLANTED_SECRET = "sk-ABCD1234ABCD1234ABCD"           # pragma: allowlist secret

_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# The normalized connector dump — the shape the connectors map INTO.           #
# (a Sent message defining a correspondent + an inbound FROM that correspondent #
#  + an inbound from a STRANGER) + a Drive doc + a Calendar event.             #
# --------------------------------------------------------------------------- #


def _dump(*, with_secret: bool = False) -> dict:
    """A representative connector dump (normalized records, the schema shape).

    Gmail: (1) a SENT message addressed to the friend — defines the friend as a
    correspondent; (2) an inbound FROM the friend — admitted; (3) an inbound from
    a STRANGER — dropped. Optionally (4) an inbound FROM the friend carrying a
    secret — admitted by the allowlist but dropped by the privacy gate. Plus one
    Drive doc and one Calendar event (no spam gate — they become Events).
    """
    gmail = [
        {
            "message_id": "sent-1",
            "sender": FOUNDER,
            "to": [FRIEND_EMAIL],
            "date": "2026-06-18T09:00:00Z",
            "body": "Kicking off the pricing thread for the launch deal.",
            "subject": "Pricing for launch",
            "is_sent": True,
        },
        {
            "message_id": "in-friend-1",
            "sender": FRIEND_EMAIL,
            "to": [FOUNDER],
            "date": "2026-06-19T10:00:00Z",
            "body": "Works for us — list price 5200 for the launch.",
            "subject": "Re: Pricing for launch",
            "is_sent": False,
        },
        {
            "message_id": "in-stranger-1",
            "sender": STRANGER_EMAIL,
            "to": [FOUNDER],
            "date": "2026-06-19T11:00:00Z",
            "body": "FLASH SALE — 50% off everything this week, claim your prize!",
            "subject": "Don't miss out",
            "is_sent": False,
        },
    ]
    if with_secret:
        gmail.append(
            {
                "message_id": "in-friend-leak",
                "sender": FRIEND_EMAIL,          # a real correspondent...
                "to": [FOUNDER],
                "date": "2026-06-20T08:00:00Z",
                "body": f"here is the demo key: {PLANTED_SECRET} — don't share",  # ...but a secret
                "subject": "key",
                "is_sent": False,
            }
        )
    return {
        "gmail": gmail,
        "drive": [
            {
                "id": "doc-1",
                "title": "Launch roadmap",
                "content": "Product launch plan targeting the beachhead segment.",
                "modified": "2026-06-17T08:00:00Z",
            }
        ],
        "calendar": [
            {
                "id": "evt-1",
                "title": "Pricing review",
                "attendees": [FOUNDER, FRIEND_EMAIL],
                "start": "2026-06-21T08:00:00Z",
                "end": "2026-06-21T09:00:00Z",
            }
        ],
    }


# --------------------------------------------------------------------------- #
# (a): the Gmail SENT-folder filter — KEEP the correspondent's inbound,         #
#      DROP the stranger's. Proven on the NORMALIZED Gmail records.             #
# --------------------------------------------------------------------------- #


def test_sent_filter_keeps_correspondent_inbound_drops_stranger():
    recs = normalized_records_from_obj(_dump())
    res = process_cloud_records(recs, user_email=FOUNDER)

    # One SENT seed, two inbound seen (friend + stranger), exactly ONE admitted.
    assert res.gmail_sent == 1
    assert res.gmail_inbound == 2
    assert res.gmail_admitted == 1, "only the correspondent's inbound may be admitted"

    # The correspondent's body became an Event; the stranger's never did.
    blob = " ".join(e.text for e in res.corpus.all_events()).lower()
    assert "list price 5200" in blob          # the friend's inbound — KEPT
    assert "flash sale" not in blob           # the stranger's inbound — DROPPED
    assert "claim your prize" not in blob


# --------------------------------------------------------------------------- #
# (b): the allowlist is SEEDED from the SENT correspondents, and it is the SAME #
#      canonical ``Allowlist`` type the message lanes use — NOT a new one.      #
# --------------------------------------------------------------------------- #


def test_allowlist_is_seeded_from_sent_correspondents_and_is_canonical():
    recs = normalized_records_from_obj(_dump())

    # Seeded from the SENT message's To/Cc only — the founder excluded, and an
    # inbound sender (the stranger) is NOT a correspondent.
    corr = correspondents_from_gmail(recs.gmail, user_email=FOUNDER)
    assert corr == {FRIEND_EMAIL}
    assert FOUNDER not in corr
    assert STRANGER_EMAIL not in corr

    res = process_cloud_records(recs, user_email=FOUNDER)

    # It IS the one canonical ingest.allowlist.Allowlist — asserted by identity,
    # not just by duck-typing (the whole point of the no-fork lesson).
    assert type(res.allowlist) is Allowlist
    assert res.allowlist.contains(FRIEND_EMAIL)        # the correspondent
    assert not res.allowlist.contains(STRANGER_EMAIL)  # a non-correspondent never matches

    # And it equals the allowlist the CANONICAL build_allowlist produces from the
    # same seed — proving the cloud lane FEEDS build_allowlist, never forks it.
    canonical = build_allowlist(sorted(corr), contacts={})
    assert res.allowlist.tokens == canonical.tokens


def test_empty_sent_means_empty_allowlist_admits_no_inbound():
    """Fail-closed: a dump with inbound but NO sent mail → no correspondents →
    an empty canonical allowlist that admits nobody (not an error)."""
    recs = normalized_records_from_obj(
        {"gmail": [
            {"message_id": "in", "sender": STRANGER_EMAIL, "to": [FOUNDER],
             "body": "hello, you don't know me", "is_sent": False},
        ]}
    )
    res = process_cloud_records(recs, user_email=FOUNDER)
    assert type(res.allowlist) is Allowlist
    assert len(res.allowlist) == 0
    assert not res.allowlist            # falsy == admits nobody
    assert res.gmail_admitted == 0


# --------------------------------------------------------------------------- #
# (c): a Drive doc and a Calendar event each become a genesis Event.            #
# --------------------------------------------------------------------------- #


def test_drive_and_calendar_records_emit_events():
    recs = normalized_records_from_obj(_dump())
    res = process_cloud_records(recs, user_email=FOUNDER)

    assert res.drive_seen == 1 and res.calendar_seen == 1
    kinds = {e.kind for e in res.corpus.all_events()}
    assert KIND_DRIVE in kinds, "the Drive doc must become an Event"
    assert KIND_CALENDAR in kinds, "the Calendar event must become an Event"

    blob = " ".join(e.text for e in res.corpus.all_events())
    assert "Launch roadmap" in blob        # the Drive doc's content
    assert "Pricing review" in blob        # the Calendar event's title


# --------------------------------------------------------------------------- #
# (d): a planted SECRET is sanitized away before it can become an Event.        #
# --------------------------------------------------------------------------- #


def test_planted_secret_is_sanitized_before_becoming_an_event():
    recs = normalized_records_from_obj(_dump(with_secret=True))
    res = process_cloud_records(recs, user_email=FOUNDER)

    # The leak came FROM the correspondent, so the allowlist ADMITTED it (2 of the
    # friend's inbound admitted: the clean reply + the leak)...
    assert res.gmail_admitted == 2
    # ...but the spine's privacy gate dropped the secret-bearing one before it
    # became an Event.
    assert res.dropped_private >= 1

    blob = " ".join(e.text for e in res.corpus.all_events())
    assert "list price 5200" in blob       # the friend's clean reply survived
    assert PLANTED_SECRET not in blob       # the secret appears in NO Event
    assert "sk-ABCD" not in blob


# --------------------------------------------------------------------------- #
# The whole chain through the cloud entrypoint (refresh.run_refresh): the four  #
# behaviors end to end → genesis → an operator-gated proposals file. NO secret, #
# NO spam, NOTHING applied.                                                     #
# --------------------------------------------------------------------------- #


def test_full_chain_through_refresh_writes_only_sanitized_correspondent_proposals(tmp_path: Path):
    import json

    recs = normalized_records_from_obj(_dump(with_secret=True))
    out = tmp_path / "proposals.json"
    summary = refresh_mod.run_refresh(recs, user_email=FOUNDER, out_path=out, today="2026-06-23")

    assert summary["written"] is True and out.is_file()
    text = out.read_text(encoding="utf-8")
    data = json.loads(text)

    # The whole file is a gate surface — proposals only, nothing applied.
    assert data["status"] == "proposed" and data["applied"] is False
    assert data["proposals"], "expected >=1 proposal from the corpus"
    assert all(p["status"] == "proposed" for p in data["proposals"])

    # The run accounting reflects (a)+(c)+(d): 1 of 2 inbound off-correspondent
    # dropped, drive + calendar seen, a secret dropped private.
    run = data["run"]
    assert run["gmail_inbound"] == 3 and run["gmail_admitted"] == 2  # friend reply + leak
    assert run["drive_seen"] == 1 and run["calendar_seen"] == 1
    assert run["dropped_private"] >= 1

    # Defense in depth: NO secret, NO stranger/spam body anywhere in the file.
    assert PLANTED_SECRET not in text
    assert "sk-ABCD" not in text
    assert "FLASH SALE" not in text
    assert "claim your prize" not in text


# --------------------------------------------------------------------------- #
# The ONE allowlist, proven across the split: the SAME canonical allowlist that #
# the cloud Gmail SENT lane SEEDS also gates a LOCAL iMessage — the union the    #
# on-ramp builds from mbox ∪ cloud-Gmail correspondents. This is the no-fork     #
# guarantee at the integration level (run.py wires exactly this).               #
# --------------------------------------------------------------------------- #


def _apple_ns(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int((dt - _APPLE_EPOCH).total_seconds() * 1_000_000_000)


def _make_chat_db(path: Path, rows: list[dict]) -> None:
    """Synthetic iMessage ``chat.db`` (the real table subset the adapter reads) —
    the same fixture builder ``test_e2e_local.py`` uses, inlined here so the cloud
    e2e can prove the cloud-seeded allowlist gates a local lane end to end."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT,
                               chat_identifier TEXT, display_name TEXT);
            CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT,
                                  attributedBody BLOB, date INTEGER,
                                  is_from_me INTEGER, handle_id INTEGER);
            CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
            """
        )
        handle_ids: dict[str, int] = {}
        chat_ids: dict[str, int] = {}
        for i, r in enumerate(rows, start=1):
            h = r.get("handle") or ""
            if h and h not in handle_ids:
                handle_ids[h] = len(handle_ids) + 1
                conn.execute("INSERT INTO handle (ROWID, id) VALUES (?, ?)", (handle_ids[h], h))
            cid = r["chat_identifier"]
            if cid not in chat_ids:
                chat_ids[cid] = len(chat_ids) + 1
                conn.execute(
                    "INSERT INTO chat (ROWID, guid, chat_identifier, display_name) VALUES (?,?,?,?)",
                    (chat_ids[cid], f"chatguid-{cid}", cid, r.get("display_name") or ""),
                )
            conn.execute(
                "INSERT INTO message (ROWID, guid, text, date, is_from_me, handle_id) VALUES (?,?,?,?,?,?)",
                (i, f"msgguid-{i}", r.get("text"), _apple_ns(r["iso"]),
                 1 if r.get("is_from_me") else 0, handle_ids.get(h)),
            )
            conn.execute(
                "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
                (chat_ids[cid], i),
            )
        conn.commit()
    finally:
        conn.close()


def test_cloud_seeded_allowlist_also_gates_local_imessage_via_run_journey(tmp_path: Path, monkeypatch):
    """The on-ramp folds the cloud dump in AND gates the local lanes by the SAME
    ONE allowlist (here seeded purely by the cloud Gmail SENT message). The
    friend (a cloud-Gmail correspondent, reachable by phone via the Contacts
    bridge) is admitted on iMessage; the stranger is dropped. Proven through the
    real ``run.run_journey`` wiring — not a hand-built allowlist."""
    import genesis_pipeline
    import agent_wiki_builder
    import run

    # Confine every write the journey performs to tmp (pillar drafts + wikis).
    monkeypatch.setattr(genesis_pipeline, "OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(agent_wiki_builder, "WIKI_ROOT", str(tmp_path / "out" / "wiki"))

    # An empty sources dir → no mbox correspondents; the cloud Gmail SENT message
    # is the SOLE seed of the one allowlist. (notes/ + mail/ absent → the on-ramp
    # falls back to the dir itself; both adapters read nothing.)
    sources = tmp_path / "sources"
    sources.mkdir()

    # The friend texts (allowlisted via the cloud-Gmail correspondent → his phone,
    # bridged the way a Mac's Contacts read would); the stranger texts (dropped).
    db = tmp_path / "chat.db"
    _make_chat_db(
        db,
        [
            {"chat_identifier": FRIEND_PHONE, "handle": FRIEND_PHONE, "display_name": "Alice",
             "text": "are we still on for friday?", "iso": "2026-06-20T10:00:00Z"},
            {"chat_identifier": STRANGER_PHONE, "handle": STRANGER_PHONE, "display_name": "Spam",
             "text": "limited time offer just for you", "iso": "2026-06-20T11:00:00Z"},
        ],
    )

    # The cloud dump's SENT message is addressed to the friend at BOTH his email
    # AND his phone, so the ONE allowlist (seeded from that SENT message's To)
    # carries his phone token — which is how his iMessage (keyed by that phone) is
    # matched. This is the faithful no-Contacts-read way to bridge email↔phone in
    # a fixture: a founder who put the friend's number in a sent mail's recipients.
    dump = {
        "gmail": [
            {"message_id": "c-sent-1", "sender": FOUNDER, "to": [FRIEND_EMAIL, FRIEND_PHONE],
             "date": "2026-06-18T09:00:00Z", "is_sent": True,
             "subject": "kickoff", "body": "great to connect, let's go."},
            {"message_id": "c-in-1", "sender": FRIEND_EMAIL, "to": [FOUNDER],
             "date": "2026-06-19T10:00:00Z", "is_sent": False,
             "subject": "Re: kickoff", "body": "excited — sending the deck shortly."},
        ],
        "drive": [],
        "calendar": [],
    }
    cloud_records = normalized_records_from_obj(dump)

    buf = io.StringIO()
    result = run.run_journey(
        sources_dir=str(sources),
        auto_ratify="none",
        today="2026-06-23",
        user_email=FOUNDER,
        local_stores={"imessage": str(db)},
        cloud_records=cloud_records,
        out_stream=buf,
    )
    printed = buf.getvalue()

    # The cloud lane ran (its result is surfaced) and admitted the friend's inbound.
    cloud = result["cloud"]
    assert cloud is not None
    assert cloud.gmail_admitted == 1            # the friend's cloud inbound — kept
    assert type(cloud.allowlist) is Allowlist   # the ONE canonical allowlist
    assert cloud.allowlist.contains(FRIEND_PHONE)  # seeded with his phone token

    # The SAME allowlist gated the LOCAL iMessage: exactly the friend's text
    # survived (the stranger's was dropped before it could become an Event).
    assert result["ingest"].kept == 1, "only the friend's iMessage should survive"
    # Nothing the stranger sent is anywhere in the printed journey.
    assert "limited time offer" not in printed


# --------------------------------------------------------------------------- #
# Absent cloud dump is a clean no-op for the on-ramp (opt-in, like local).      #
# --------------------------------------------------------------------------- #


def test_run_journey_without_cloud_dump_is_unchanged(tmp_path: Path, monkeypatch):
    """The cloud fold is OPT-IN: with no ``cloud_records`` the on-ramp behaves
    exactly as before — ``result['cloud']`` is None and the sample still seeds."""
    import genesis_pipeline
    import agent_wiki_builder
    import run

    monkeypatch.setattr(genesis_pipeline, "OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(agent_wiki_builder, "WIKI_ROOT", str(tmp_path / "out" / "wiki"))

    buf = io.StringIO()
    result = run.run_journey(
        sources_dir=run.SAMPLE_DIR,
        auto_ratify="none",
        today="2026-06-23",
        out_stream=buf,
    )
    assert result["cloud"] is None
    assert result["packet"] is not None
    assert result["ingest"].kept >= 5
