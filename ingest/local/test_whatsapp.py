"""Tests for the WhatsApp local adapter against a SYNTHETIC WhatsApp Desktop store.

The real ``ChatStorage.sqlite`` / ``ContactsV2.sqlite`` need macOS Full Disk
Access (an un-scriptable manual grant), so these build a synthetic SQLite fixture
that recreates the schema SUBSET the adapter reads — ``ZWACHATSESSION`` /
``ZWAMESSAGE`` / ``ZWAGROUPMEMBER`` (messages DB) and ``ZWAADDRESSBOOKCONTACT``
(contacts DB) — and exercise the parse / JID-resolution / allowlist-filter /
egress-sanitize / Event-build path end to end.

Coverage (the load-bearing behaviors of Lane D):
  * a 1:1 chat with an allowlisted counterparty is ingested;
  * a 1:1 chat with a NON-allowlisted counterparty is dropped whole;
  * a group chat is admitted because ONE member is allowlisted, and inside it a
    NON-allowlisted sender's message is dropped while the allowlisted member's and
    the founder's own (``ZISFROMME``) are kept;
  * a JID is matched via its ContactsV2 phone AND via its own numeric user-part
    (never as a raw email-shaped JID);
  * an egress-private body is dropped;
  * with NO allowlist injected the lane is fail-closed (yields nothing);
  * ``read_events()`` produces genesis Events;
  * a missing store yields nothing.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ingest.allowlist import build_allowlist
from ingest.local.whatsapp import WhatsAppAdapter

# --------------------------------------------------------------------------- #
# Synthetic fixture builders (the real schema subset the adapter reads)        #
# --------------------------------------------------------------------------- #

_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def _apple_seconds(iso_utc: str) -> float:
    """A real ZMESSAGEDATE: seconds since the Apple epoch for an ISO-UTC time."""
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    return (dt - _APPLE_EPOCH).total_seconds()


def _build_chat_store(path: Path, sessions, messages, *, with_group_member=True) -> None:
    """Create a synthetic ChatStorage.sqlite.

    ``sessions``: list of (z_pk, contact_jid, partner_name, removed).
    ``messages``: list of dicts with keys z_pk, session, date, is_from_me, text,
        from_jid, to_jid, group_member (Z_PK into ZWAGROUPMEMBER or None).
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE ZWACHATSESSION ("
            "Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT, ZPARTNERNAME TEXT, "
            "ZREMOVED INTEGER, ZLASTMESSAGEDATE REAL)"
        )
        conn.execute(
            "CREATE TABLE ZWAMESSAGE ("
            "Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZMESSAGEDATE REAL, "
            "ZISFROMME INTEGER, ZTEXT TEXT, ZFROMJID TEXT, ZTOJID TEXT, "
            "ZGROUPMEMBER INTEGER)"
        )
        if with_group_member:
            conn.execute(
                "CREATE TABLE ZWAGROUPMEMBER ("
                "Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZMEMBERJID TEXT, "
                "ZCONTACTNAME TEXT, ZFIRSTNAME TEXT)"
            )
        for z_pk, jid, name, removed in sessions:
            conn.execute(
                "INSERT INTO ZWACHATSESSION (Z_PK, ZCONTACTJID, ZPARTNERNAME, ZREMOVED) "
                "VALUES (?,?,?,?)",
                (z_pk, jid, name, removed),
            )
        for m in messages:
            conn.execute(
                "INSERT INTO ZWAMESSAGE (Z_PK, ZCHATSESSION, ZMESSAGEDATE, ZISFROMME, "
                "ZTEXT, ZFROMJID, ZTOJID, ZGROUPMEMBER) VALUES (?,?,?,?,?,?,?,?)",
                (
                    m["z_pk"], m["session"], m["date"], m["is_from_me"], m["text"],
                    m.get("from_jid"), m.get("to_jid"), m.get("group_member"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _add_group_members(path: Path, members) -> None:
    """``members``: list of (z_pk, session, member_jid, contact_name, first_name)."""
    conn = sqlite3.connect(str(path))
    try:
        for z_pk, session, jid, contact_name, first_name in members:
            conn.execute(
                "INSERT INTO ZWAGROUPMEMBER (Z_PK, ZCHATSESSION, ZMEMBERJID, "
                "ZCONTACTNAME, ZFIRSTNAME) VALUES (?,?,?,?,?)",
                (z_pk, session, jid, contact_name, first_name),
            )
        conn.commit()
    finally:
        conn.close()


def _build_contacts_store(path: Path, contacts) -> None:
    """Create a synthetic ContactsV2.sqlite.

    ``contacts``: list of (whatsapp_id, lid, phone, localized_phone, full_name, username).
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE ZWAADDRESSBOOKCONTACT ("
            "Z_PK INTEGER PRIMARY KEY, ZWHATSAPPID TEXT, ZLID TEXT, ZPHONENUMBER TEXT, "
            "ZLOCALIZEDPHONENUMBER TEXT, ZFULLNAME TEXT, ZUSERNAME TEXT)"
        )
        for i, (wid, lid, phone, localized, full_name, username) in enumerate(contacts, start=1):
            conn.execute(
                "INSERT INTO ZWAADDRESSBOOKCONTACT (Z_PK, ZWHATSAPPID, ZLID, "
                "ZPHONENUMBER, ZLOCALIZEDPHONENUMBER, ZFULLNAME, ZUSERNAME) "
                "VALUES (?,?,?,?,?,?,?)",
                (i, wid, lid, phone, localized, full_name, username),
            )
        conn.commit()
    finally:
        conn.close()


# Reusable JIDs / identities for the fixtures.
_ALICE_JID = "14155550101@s.whatsapp.net"   # allowlisted (phone 4155550101)
_BOB_JID = "14155550102@s.whatsapp.net"     # allowlisted via contact phone
_STRANGER_JID = "19998887777@s.whatsapp.net"  # NOT allowlisted
_GROUP_JID = "120363000000000000@g.us"


def _allowlist():
    # Alice is allowlisted by phone; Bob is allowlisted by email but reaches via a
    # phone the email lane resolved through Contacts (the ``contacts`` bridge).
    return build_allowlist(
        ["+1 (415) 555-0101", "bob@example.com"],
        contacts={"bob@example.com": ["+1 415 555 0102"]},
    )


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #


def test_one_to_one_allowlisted_chat_is_ingested(tmp_path: Path):
    db = tmp_path / "ChatStorage.sqlite"
    _build_chat_store(
        db,
        sessions=[(1, _ALICE_JID, "Alice", 0)],
        messages=[
            {"z_pk": 10, "session": 1, "date": _apple_seconds("2026-06-20T10:00:00Z"),
             "is_from_me": 0, "text": "hey are we still on for friday?",
             "from_jid": _ALICE_JID, "to_jid": None, "group_member": None},
            {"z_pk": 11, "session": 1, "date": _apple_seconds("2026-06-20T10:01:00Z"),
             "is_from_me": 1, "text": "yes locking it in", "from_jid": None,
             "to_jid": _ALICE_JID, "group_member": None},
        ],
    )
    records = list(WhatsAppAdapter(db, allowlist=_allowlist(), contacts_db_path=None).read())
    assert len(records) == 2
    incoming = next(r for r in records if not r["is_from_me"])
    assert incoming["kind"] == "whatsapp"
    assert incoming["source_id"] == "wa-10"
    assert "friday" in incoming["text"]
    assert incoming["observed_at"] == "2026-06-20T10:00:00Z"
    assert incoming["meta"]["sender"] == "Alice"        # resolved label, not the JID
    assert "@s.whatsapp.net" not in incoming["meta"]["sender"]
    # the founder's own message is labeled self
    outgoing = next(r for r in records if r["is_from_me"])
    assert outgoing["meta"]["sender"] == "self"


def test_non_allowlisted_one_to_one_chat_is_dropped(tmp_path: Path):
    db = tmp_path / "ChatStorage.sqlite"
    _build_chat_store(
        db,
        sessions=[(1, _STRANGER_JID, "Telemarketer", 0)],
        messages=[
            {"z_pk": 10, "session": 1, "date": _apple_seconds("2026-06-20T10:00:00Z"),
             "is_from_me": 0, "text": "limited time offer just for you",
             "from_jid": _STRANGER_JID, "to_jid": None, "group_member": None},
        ],
    )
    records = list(WhatsAppAdapter(db, allowlist=_allowlist(), contacts_db_path=None).read())
    assert records == []  # the whole chat is dropped — not on the allowlist


def test_group_admitted_by_one_member_drops_non_allowlisted_sender(tmp_path: Path):
    db = tmp_path / "ChatStorage.sqlite"
    _build_chat_store(
        db,
        sessions=[(5, _GROUP_JID, "Launch Team", 0)],
        messages=[
            # Alice (allowlisted) speaks -> kept
            {"z_pk": 50, "session": 5, "date": _apple_seconds("2026-06-21T09:00:00Z"),
             "is_from_me": 0, "text": "ship list looks good", "from_jid": None,
             "to_jid": None, "group_member": 1},
            # Stranger (NOT allowlisted) speaks in the same admitted group -> dropped
            {"z_pk": 51, "session": 5, "date": _apple_seconds("2026-06-21T09:05:00Z"),
             "is_from_me": 0, "text": "who is this", "from_jid": None,
             "to_jid": None, "group_member": 2},
            # Founder speaks -> kept
            {"z_pk": 52, "session": 5, "date": _apple_seconds("2026-06-21T09:10:00Z"),
             "is_from_me": 1, "text": "thanks team", "from_jid": None,
             "to_jid": None, "group_member": None},
        ],
    )
    _add_group_members(
        db,
        members=[
            (1, 5, _ALICE_JID, "Alice", "Alice"),
            (2, 5, _STRANGER_JID, "Unknown", None),
        ],
    )
    records = list(WhatsAppAdapter(db, allowlist=_allowlist(), contacts_db_path=None).read())
    texts = {r["text"] for r in records}
    assert texts == {"ship list looks good", "thanks team"}  # stranger's dropped
    assert all(r["meta"]["is_group"] for r in records)
    alice_msg = next(r for r in records if r["text"] == "ship list looks good")
    assert alice_msg["meta"]["sender"] == "Alice"


def test_sender_resolved_via_contactsv2_phone(tmp_path: Path):
    # Bob's JID is numeric; ContactsV2 maps it to his phone + full name. He's
    # allowlisted via that phone (bridged from his email by the contacts arg).
    db = tmp_path / "ChatStorage.sqlite"
    contacts_db = tmp_path / "ContactsV2.sqlite"
    _build_chat_store(
        db,
        sessions=[(2, _BOB_JID, "", 0)],  # no partner name -> contact must supply it
        messages=[
            {"z_pk": 20, "session": 2, "date": _apple_seconds("2026-06-22T08:00:00Z"),
             "is_from_me": 0, "text": "sending the signed doc now", "from_jid": _BOB_JID,
             "to_jid": None, "group_member": None},
        ],
    )
    _build_contacts_store(
        contacts_db,
        contacts=[("14155550102", "14155550102@lid", "+14155550102",
                   "(415) 555-0102", "Bob Vendor", "bobv")],
    )
    records = list(WhatsAppAdapter(db, allowlist=_allowlist(), contacts_db_path=contacts_db).read())
    assert len(records) == 1
    assert records[0]["meta"]["sender"] == "Bob Vendor"     # name came from ContactsV2
    assert records[0]["chat_name"] == "Bob Vendor"
    assert "Bob Vendor" in records[0]["participants"]


def test_private_body_is_dropped_by_egress(tmp_path: Path):
    db = tmp_path / "ChatStorage.sqlite"
    _build_chat_store(
        db,
        sessions=[(1, _ALICE_JID, "Alice", 0)],
        messages=[
            {"z_pk": 10, "session": 1, "date": _apple_seconds("2026-06-20T10:00:00Z"),
             "is_from_me": 0, "text": "here's the key: sk-ABCDEFGHIJKLMNOarP123456",  # pragma: allowlist secret
             "from_jid": _ALICE_JID, "to_jid": None, "group_member": None},
            {"z_pk": 11, "session": 1, "date": _apple_seconds("2026-06-20T10:01:00Z"),
             "is_from_me": 0, "text": "and a normal harmless message",
             "from_jid": _ALICE_JID, "to_jid": None, "group_member": None},
        ],
    )
    records = list(WhatsAppAdapter(db, allowlist=_allowlist(), contacts_db_path=None).read())
    texts = [r["text"] for r in records]
    assert texts == ["and a normal harmless message"]  # the secret-bearing body dropped


def test_dumb_reader_vs_empty_allowlist(tmp_path: Path):
    # The two contracts:
    #  * allowlist=None -> DUMB READER (the sync spine filters): yields the row.
    #  * an EMPTY injected Allowlist -> FILTERED + fail-closed: yields nothing.
    db = tmp_path / "ChatStorage.sqlite"
    _build_chat_store(
        db,
        sessions=[(1, _ALICE_JID, "Alice", 0)],
        messages=[
            {"z_pk": 10, "session": 1, "date": _apple_seconds("2026-06-20T10:00:00Z"),
             "is_from_me": 0, "text": "hello", "from_jid": _ALICE_JID,
             "to_jid": None, "group_member": None},
        ],
    )
    # Dumb-reader mode (no allowlist) surfaces the row unfiltered for the spine.
    dumb = list(WhatsAppAdapter(db).read())
    assert len(dumb) == 1
    assert dumb[0]["chat_id"] == _ALICE_JID
    assert dumb[0]["source_id"] == "wa-10"
    # An empty injected allowlist permits nobody -> nothing ingested (fail-closed).
    assert list(WhatsAppAdapter(db, allowlist=build_allowlist([])).read()) == []


def test_read_events_yields_genesis_events(tmp_path: Path):
    db = tmp_path / "ChatStorage.sqlite"
    _build_chat_store(
        db,
        sessions=[(1, _ALICE_JID, "Alice", 0)],
        messages=[
            {"z_pk": 10, "session": 1, "date": _apple_seconds("2026-06-20T10:00:00Z"),
             "is_from_me": 0, "text": "launch_date = 2026-10-15", "from_jid": _ALICE_JID,
             "to_jid": None, "group_member": None},
        ],
    )
    events = list(WhatsAppAdapter(db, allowlist=_allowlist(), contacts_db_path=None).read_events())
    assert len(events) == 1
    ev = events[0]
    # genesis Event surface
    assert ev.kind == "whatsapp"
    assert ev.source_id == "wa-10"
    assert ev.observed_at == "2026-06-20T10:00:00Z"
    assert "launch_date = 2026-10-15" in ev.text
    assert ev.anchor().source_id == "wa-10"


def test_missing_store_yields_nothing(tmp_path: Path):
    adapter = WhatsAppAdapter(tmp_path / "does-not-exist.sqlite", allowlist=_allowlist())
    assert list(adapter.read()) == []
    assert list(adapter.read_events()) == []
