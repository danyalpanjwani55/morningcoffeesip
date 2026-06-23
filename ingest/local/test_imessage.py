"""Tests for the local iMessage lane, against a SYNTHETIC ``chat.db``.

The real ``~/Library/Messages/chat.db`` is behind macOS **Full Disk Access** — an
un-scriptable manual grant — so (per the build contract) the CODE is exercised
against a tiny synthetic SQLite database built to match the real schema subset
the adapter reads:

    handle(ROWID, id)                       -- id = phone/email
    chat(ROWID, guid, chat_identifier, display_name)
    message(ROWID, guid, text, attributedBody, date, is_from_me, handle_id)
    chat_message_join(chat_id, message_id)
    chat_handle_join(chat_id, handle_id)    -- explicit chat membership

The load-bearing proof (the whole reason the allowlist exists): a message from a
**non-allowlisted handle is DROPPED**. We prove that against the one allowlist the
whole system turns on — the identity ``Allowlist`` (``ingest/allowlist.py``),
built from the founder's SENT-folder correspondents and shared by every message
lane — gating on ``Allowlist.contains`` over the record's ``chat_id`` exactly as
``ingest/local/sync.py`` does. The survivors become sanitized genesis ``Event``s,
while a secret-bearing message is dropped by the egress gate.

Run: ``rm -rf ~/Library/Caches/com.apple.python 2>/dev/null;
/usr/bin/python3 -B -m pytest -q``
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ingest.allowlist import build_allowlist
from ingest.local import imessage_adapter
from ingest.local.imessage import IMessageAdapter, _apple_date_to_iso
from ingest.pipeline import ingest_records

# Two handles: one the founder corresponds with, one a stranger.
_ALLOWED_HANDLE = "+14155550101"
_STRANGER_HANDLE = "+19998887777"

# Nanoseconds since the Apple epoch (2001-01-01 UTC) — the modern chat.db scale.
_DATE_NS = 770_000_000 * 1_000_000_000


def _make_chat_db(path: Path) -> None:
    """Create a synthetic chat.db with the real schema subset + a few messages.

    chat 10 (allowed friend, handle 1) has an inbound + the founder's outbound;
    chat 20 (stranger, handle 2) has one inbound that MUST be dropped.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
            CREATE TABLE chat (
                ROWID INTEGER PRIMARY KEY, guid TEXT,
                chat_identifier TEXT, display_name TEXT
            );
            CREATE TABLE message (
                ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT,
                attributedBody BLOB, date INTEGER, is_from_me INTEGER,
                handle_id INTEGER
            );
            CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
            CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
            """
        )
        conn.execute(
            "INSERT INTO handle (ROWID, id) VALUES (1, ?), (2, ?)",
            (_ALLOWED_HANDLE, _STRANGER_HANDLE),
        )
        conn.execute(
            "INSERT INTO chat (ROWID, guid, chat_identifier, display_name) "
            "VALUES (10, 'g1', ?, 'Allowed Friend'), (20, 'g2', ?, 'Stranger')",
            (_ALLOWED_HANDLE, _STRANGER_HANDLE),
        )
        conn.execute(
            "INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (10, 1), (20, 2)"
        )
        conn.execute(
            "INSERT INTO message (ROWID, guid, text, date, is_from_me, handle_id) "
            "VALUES (100, 'm100', 'hey are we still on for tomorrow', ?, 0, 1)",
            (_DATE_NS,),
        )
        conn.execute(
            "INSERT INTO message (ROWID, guid, text, date, is_from_me, handle_id) "
            "VALUES (101, 'm101', 'yes see you then', ?, 1, 1)",
            (_DATE_NS + 1,),
        )
        conn.execute(
            "INSERT INTO message (ROWID, guid, text, date, is_from_me, handle_id) "
            "VALUES (200, 'm200', 'you do not know me', ?, 0, 2)",
            (_DATE_NS + 2,),
        )
        conn.execute(
            "INSERT INTO chat_message_join (chat_id, message_id) "
            "VALUES (10, 100), (10, 101), (20, 200)"
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# The adapter reads the synthetic chat.db                                      #
# --------------------------------------------------------------------------- #


def test_adapter_reads_synthetic_chat_db(tmp_path: Path):
    db = tmp_path / "chat.db"
    _make_chat_db(db)

    records = list(IMessageAdapter(db).read())
    # All three messages surface as raw records (filtering is a later stage).
    assert len(records) == 3
    texts = {r["text"] for r in records}
    assert "hey are we still on for tomorrow" in texts
    assert "you do not know me" in texts

    inbound = next(r for r in records if r["source_id"] == "m100")
    assert inbound["kind"] == "imessage"
    assert inbound["chat_id"] == _ALLOWED_HANDLE
    assert inbound["meta"]["is_from_me"] is False
    assert inbound["observed_at"].endswith("Z")  # Apple epoch -> ISO UTC

    outbound = next(r for r in records if r["source_id"] == "m101")
    assert outbound["meta"]["is_from_me"] is True  # the founder's own send


def test_adapter_accessor_matches_class(tmp_path: Path):
    # The lazy package accessor builds the same adapter the registry uses.
    db = tmp_path / "chat.db"
    _make_chat_db(db)
    assert isinstance(imessage_adapter(db), IMessageAdapter)


def test_adapter_missing_db_yields_nothing(tmp_path: Path):
    # An absent store is a clean no-op for the lane (graceful absence).
    assert list(IMessageAdapter(tmp_path / "does-not-exist.db").read()) == []


def test_adapter_read_only_does_not_mutate(tmp_path: Path):
    # The live Messages store must never be written. Reading leaves it byte-identical.
    db = tmp_path / "chat.db"
    _make_chat_db(db)
    before = db.read_bytes()
    list(IMessageAdapter(db).read())
    assert db.read_bytes() == before


# --------------------------------------------------------------------------- #
# THE PROOF: a non-allowlisted handle is dropped                               #
# --------------------------------------------------------------------------- #


def test_allowlist_drops_non_allowlisted_handle(tmp_path: Path):
    """The live sync filter (``ingest/local/sync.py``): scope to the allowed
    correspondent -> the stranger is gone. Gated by the shared identity
    ``Allowlist.contains`` over the record's ``chat_id``, exactly as sync.py."""
    db = tmp_path / "chat.db"
    _make_chat_db(db)
    records = list(IMessageAdapter(db).read())

    allow = build_allowlist([_ALLOWED_HANDLE], contacts={})
    scoped = [r for r in records if allow.contains(r["chat_id"])]

    assert len(scoped) == 2  # the two allowed-chat messages
    assert all(r["chat_id"] == _ALLOWED_HANDLE for r in scoped)
    assert all(r["chat_id"] != _STRANGER_HANDLE for r in scoped)  # stranger DROPPED
    assert "you do not know me" not in {r["text"] for r in scoped}


def test_empty_allowlist_drops_everything(tmp_path: Path):
    """Fail-closed: an empty correspondent allowlist puts NOTHING in scope (opt-in,
    not opt-out) — no correspondents means the lane ingests nothing."""
    db = tmp_path / "chat.db"
    _make_chat_db(db)
    records = list(IMessageAdapter(db).read())

    allow = build_allowlist([], contacts={})  # no correspondents
    assert not allow  # falsy == admits nobody
    assert [r for r in records if allow.contains(r["chat_id"])] == []


def test_identity_allowlist_contains_drops_stranger_handle(tmp_path: Path):
    """The shared identity contract (ingest/allowlist.py): the allowed phone is a
    correspondent (even reformatted), the stranger's handle is NOT — so a lane
    filtering by ``Allowlist.contains`` on the sender handle drops the stranger.
    """
    db = tmp_path / "chat.db"
    _make_chat_db(db)
    records = list(IMessageAdapter(db).read())

    # Built from the founder's SENT-folder correspondents (here just the friend),
    # contacts={} to keep it pure (no macOS AddressBook read in the test).
    allow = build_allowlist(["+1 (415) 555-0101"], contacts={})
    assert allow.contains(_ALLOWED_HANDLE) is True       # reformatted -> same person
    assert allow.contains(_STRANGER_HANDLE) is False     # not a correspondent

    kept = [r for r in records if allow.contains(r["chat_id"])]
    assert {r["source_id"] for r in kept} == {"m100", "m101"}
    assert "m200" not in {r["source_id"] for r in kept}  # stranger DROPPED


# --------------------------------------------------------------------------- #
# Survivors -> sanitized genesis Events (through the shared spine)             #
# --------------------------------------------------------------------------- #


def test_allowlisted_messages_become_genesis_events(tmp_path: Path):
    db = tmp_path / "chat.db"
    _make_chat_db(db)
    records = list(IMessageAdapter(db).read())

    allow = build_allowlist([_ALLOWED_HANDLE], contacts={})
    scoped = [r for r in records if allow.contains(r["chat_id"])]

    result = ingest_records(scoped)
    assert result.kept == 2
    events = result.corpus.all_events()
    assert {e.kind for e in events} == {"imessage"}
    assert {e.text for e in events} == {
        "hey are we still on for tomorrow",
        "yes see you then",
    }
    # The Event carries the counterparty handle as a participant.
    assert any(_ALLOWED_HANDLE in e.participants for e in events)
    # And a sortable UTC timestamp derived from the Apple-epoch date.
    assert all(e.observed_at.endswith("Z") for e in events)


def test_secret_bearing_message_is_dropped_by_egress(tmp_path: Path):
    """A message carrying a credential never becomes an Event (fail-closed),
    even from an allowlisted chat — the privacy gate is independent of scope."""
    db = tmp_path / "chat.db"
    _make_chat_db(db)
    # Add a secret-bearing message to the ALLOWED chat (handle 1, chat 10).
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO message (ROWID, guid, text, date, is_from_me, handle_id) "
            "VALUES (102, 'm102', ?, ?, 0, 1)",
            ("the api_key = sk-ABCD1234ABCD1234ABCD", _DATE_NS + 3),  # pragma: allowlist secret
        )
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (10, 102)")
        conn.commit()
    finally:
        conn.close()

    records = list(IMessageAdapter(db).read())
    allow = build_allowlist([_ALLOWED_HANDLE], contacts={})
    scoped = [r for r in records if allow.contains(r["chat_id"])]

    result = ingest_records(scoped)
    assert result.dropped_private == 1  # the secret message
    assert result.kept == 2             # the two clean messages survive
    assert all("sk-ABCD" not in e.text for e in result.corpus.all_events())


# --------------------------------------------------------------------------- #
# Apple-epoch timestamp conversion (the one fiddly numeric bit)                #
# --------------------------------------------------------------------------- #


def test_apple_date_handles_nanoseconds_and_seconds_and_garbage():
    # Modern chat.db: nanoseconds since 2001-01-01.
    ns = _apple_date_to_iso(_DATE_NS)
    assert ns.endswith("Z") and ns.startswith("20")
    # Older rows: whole seconds since the same epoch -> same instant.
    sec = _apple_date_to_iso(770_000_000)
    assert sec == ns
    # Null / zero / non-numeric -> "" (normalize falls back to ingested_at).
    assert _apple_date_to_iso(None) == ""
    assert _apple_date_to_iso(0) == ""
    assert _apple_date_to_iso("not-a-number") == ""
