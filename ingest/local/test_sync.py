"""Tests for the local Mac sync agent entrypoint (``ingest.local.sync``).

The sync agent is the LOCAL half of the architecture split: it reads the two
on-device stores (iMessage ``chat.db`` + WhatsApp ``ChatStorage.sqlite``),
correspondent-allowlists them, and writes sanitized genesis Events to a local
JSONL store — proposals-only, nothing sent.

Everything runs against SYNTHETIC SQLite fixtures matching the real schema subset
each adapter reads — the live stores need Full Disk Access (an un-scriptable
manual grant, documented in docs/INGEST-ARCHITECTURE.md), so the code is
exercised against fixtures, never the real stores.

The headline acceptance the goal-loop asks for: ``run_sync(dry_run=True)`` over
ABSENT and EMPTY stores is a clean no-op (no Events, no file written). The other
tests prove the allowlist actually gates, the store write is idempotent, secrets
are dropped, and an unavailable lane is skipped (not a crash).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ingest.allowlist import build_allowlist
from ingest.local import sync as sync_mod


# --------------------------------------------------------------------------- #
# Synthetic fixtures — the real schema SUBSET each adapter reads               #
# --------------------------------------------------------------------------- #

_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def _apple_ns(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int((dt - _APPLE_EPOCH).total_seconds() * 1_000_000_000)


def _apple_s(iso: str) -> float:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (dt - _APPLE_EPOCH).total_seconds()


# A correspondent + a stranger, used across the lanes.
_FRIEND_PHONE = "+14155550101"          # what the founder corresponds with
_FRIEND_HANDLE = "14155550101@s.whatsapp.net"
_STRANGER_HANDLE = "+19998887777"
_STRANGER_JID = "19998887777@s.whatsapp.net"


def _allowlist():
    """The founder corresponds with the friend (phone), nobody else. contacts={}
    keeps the build pure (no macOS Contacts read in tests)."""
    return build_allowlist([_FRIEND_PHONE], contacts={})


def make_chat_db(path: Path, rows: list[dict]) -> None:
    """Synthetic ``chat.db`` with the real table subset.

    Each row: {chat_identifier, display_name, handle, text, iso, is_from_me, guid}.
    """
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
                (i, r.get("guid") or f"msgguid-{i}", r.get("text"), _apple_ns(r["iso"]),
                 1 if r.get("is_from_me") else 0, handle_ids.get(h)),
            )
            conn.execute(
                "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
                (chat_ids[cid], i),
            )
        conn.commit()
    finally:
        conn.close()


def make_whatsapp_store(path: Path, sessions: list[dict], messages: list[dict]) -> None:
    """Synthetic WhatsApp ``ChatStorage.sqlite`` (the schema the adapter queries:
    ZWACHATSESSION with ZREMOVED, ZWAMESSAGE with ZGROUPMEMBER, ZWAGROUPMEMBER).

    ``sessions``: dicts {z_pk, contact_jid, partner_name}.
    ``messages``: dicts {z_pk, session, iso, is_from_me, text, from_jid}.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT,
                                         ZPARTNERNAME TEXT, ZREMOVED INTEGER,
                                         ZLASTMESSAGEDATE REAL);
            CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER,
                                     ZMESSAGEDATE REAL, ZISFROMME INTEGER, ZTEXT TEXT,
                                     ZFROMJID TEXT, ZTOJID TEXT, ZGROUPMEMBER INTEGER);
            CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER,
                                         ZMEMBERJID TEXT, ZCONTACTNAME TEXT, ZFIRSTNAME TEXT);
            """
        )
        for s in sessions:
            conn.execute(
                "INSERT INTO ZWACHATSESSION (Z_PK, ZCONTACTJID, ZPARTNERNAME, ZREMOVED) VALUES (?,?,?,0)",
                (s["z_pk"], s["contact_jid"], s.get("partner_name") or ""),
            )
        for m in messages:
            conn.execute(
                "INSERT INTO ZWAMESSAGE (Z_PK, ZCHATSESSION, ZMESSAGEDATE, ZISFROMME, ZTEXT, ZFROMJID) "
                "VALUES (?,?,?,?,?,?)",
                (m["z_pk"], m["session"], _apple_s(m["iso"]),
                 1 if m.get("is_from_me") else 0, m.get("text"), m.get("from_jid") or ""),
            )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Lazy-import contract (the build-order-race guard)                            #
# --------------------------------------------------------------------------- #


def test_package_imports_without_hard_dep_on_adapters():
    import importlib

    pkg = importlib.import_module("ingest.local")
    assert "imessage_adapter" in pkg.__all__
    assert callable(pkg.imessage_adapter)
    assert callable(pkg.whatsapp_adapter)
    assert pkg.available_lanes() == ("imessage", "whatsapp")


def test_local_adapters_skips_unavailable_lane(monkeypatch):
    import ingest.local as pkg

    def _boom(_path=None, **_kw):
        raise pkg.LocalAdapterUnavailable("simulated missing module")

    monkeypatch.setitem(pkg._LANE_FACTORIES, "whatsapp", _boom)
    built = pkg.local_adapters()
    lanes = [lane for lane, _ in built]
    assert "imessage" in lanes
    assert "whatsapp" not in lanes


# --------------------------------------------------------------------------- #
# run_sync — the headline acceptance: clean no-op on absent / empty stores     #
# --------------------------------------------------------------------------- #


def test_dry_run_on_absent_stores_is_clean_noop(tmp_path: Path):
    store = tmp_path / "events.jsonl"
    res = sync_mod.run_sync(
        paths={"imessage": tmp_path / "nope.db", "whatsapp": tmp_path / "nope.sqlite"},
        allowlist=_allowlist(),
        store_path=store,
        dry_run=True,
    )
    assert res.dry_run is True
    assert res.total_kept == 0
    assert res.written == 0
    assert not store.exists()  # dry-run never creates the file
    # Both lanes read nothing (absent stores).
    for lane in res.lanes:
        assert lane.read == 0
        assert lane.kept == 0


def test_dry_run_on_empty_stores_is_clean_noop(tmp_path: Path):
    db = tmp_path / "chat.db"
    make_chat_db(db, [])
    wa = tmp_path / "ChatStorage.sqlite"
    make_whatsapp_store(wa, [], [])
    out = tmp_path / "events.jsonl"
    res = sync_mod.run_sync(
        paths={"imessage": db, "whatsapp": wa},
        allowlist=_allowlist(),
        store_path=out,
        dry_run=True,
    )
    assert res.total_kept == 0
    assert res.written == 0
    assert not out.exists()
    for lane in res.lanes:
        assert lane.read == 0


def test_empty_allowlist_ingests_nothing_even_with_messages(tmp_path: Path):
    # An empty correspondent allowlist => nothing admitted (fail-closed), even
    # though the store has messages.
    db = tmp_path / "chat.db"
    make_chat_db(
        db,
        [{"chat_identifier": _FRIEND_PHONE, "handle": _FRIEND_PHONE, "text": "hi",
          "iso": "2026-06-20T10:00:00Z"}],
    )
    out = tmp_path / "events.jsonl"
    res = sync_mod.run_sync(
        lanes=["imessage"],
        paths={"imessage": db},
        allowlist=build_allowlist([], contacts={}),  # empty
        store_path=out,
        dry_run=False,
    )
    assert res.written == 0
    assert not out.exists()
    [lane] = res.lanes
    assert lane.read == 1            # it READ the row
    assert lane.after_allowlist == 0  # but admitted none


# --------------------------------------------------------------------------- #
# run_sync — iMessage lane writes correctly + sanitizes + idempotent           #
# --------------------------------------------------------------------------- #


def test_imessage_lane_writes_allowlisted_and_drops_stranger_and_secret(tmp_path: Path):
    db = tmp_path / "chat.db"
    make_chat_db(
        db,
        [
            {"chat_identifier": _FRIEND_PHONE, "handle": _FRIEND_PHONE, "display_name": "Friend",
             "text": "launch_date = 2026-10-15", "iso": "2026-06-20T10:00:00Z"},
            # stranger chat -> dropped by the correspondent allowlist
            {"chat_identifier": _STRANGER_HANDLE, "handle": _STRANGER_HANDLE,
             "text": "you do not know me", "iso": "2026-06-20T11:00:00Z"},
            # a secret in the FRIEND chat -> dropped by the egress gate
            {"chat_identifier": _FRIEND_PHONE, "handle": _FRIEND_PHONE, "display_name": "Friend",
             "text": "api_key = sk-ABCD1234ABCD1234ABCD",  # pragma: allowlist secret
             "iso": "2026-06-20T12:00:00Z"},
        ],
    )
    out = tmp_path / "events.jsonl"
    res = sync_mod.run_sync(
        lanes=["imessage"],
        paths={"imessage": db},
        allowlist=_allowlist(),
        store_path=out,
        dry_run=False,
    )
    [lane] = res.lanes
    assert lane.read == 3
    assert lane.after_allowlist == 2     # stranger dropped pre-sanitize
    assert lane.kept == 1                 # the secret-bearing one dropped by egress
    assert lane.dropped_private == 1
    assert res.written == 1
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1
    assert "launch_date = 2026-10-15" in rows[0]["text"]
    blob = out.read_text(encoding="utf-8")
    assert "you do not know me" not in blob   # stranger never stored
    assert "sk-ABCD" not in blob              # secret never stored


def test_write_is_idempotent(tmp_path: Path):
    db = tmp_path / "chat.db"
    make_chat_db(
        db,
        [{"chat_identifier": _FRIEND_PHONE, "handle": _FRIEND_PHONE,
          "text": "hello world", "iso": "2026-06-20T10:00:00Z"}],
    )
    out = tmp_path / "events.jsonl"
    kw = dict(lanes=["imessage"], paths={"imessage": db}, allowlist=_allowlist(),
              store_path=out, dry_run=False)
    r1 = sync_mod.run_sync(**kw)
    assert r1.written == 1
    r2 = sync_mod.run_sync(**kw)  # second run: same Event id already in store
    assert r2.written == 0
    rows = [l for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1  # not duplicated


# --------------------------------------------------------------------------- #
# run_sync — WhatsApp lane wires through (adapter filters internally)          #
# --------------------------------------------------------------------------- #


def test_whatsapp_lane_writes_allowlisted_and_drops_stranger(tmp_path: Path):
    wa = tmp_path / "ChatStorage.sqlite"
    make_whatsapp_store(
        wa,
        sessions=[
            {"z_pk": 1, "contact_jid": _FRIEND_HANDLE, "partner_name": "Friend"},
            {"z_pk": 2, "contact_jid": _STRANGER_JID, "partner_name": "Stranger"},
        ],
        messages=[
            {"z_pk": 10, "session": 1, "iso": "2026-06-19T09:00:00Z", "is_from_me": False,
             "text": "invoice attached", "from_jid": _FRIEND_HANDLE},
            {"z_pk": 11, "session": 2, "iso": "2026-06-19T10:00:00Z", "is_from_me": False,
             "text": "spam from a stranger", "from_jid": _STRANGER_JID},
        ],
    )
    out = tmp_path / "events.jsonl"
    res = sync_mod.run_sync(
        lanes=["whatsapp"],
        paths={"whatsapp": wa},
        allowlist=_allowlist(),
        store_path=out,
        dry_run=False,
    )
    [lane] = res.lanes
    # The adapter already dropped the stranger chat internally -> only the friend's.
    assert lane.kept == 1
    assert res.written == 1
    blob = out.read_text(encoding="utf-8")
    assert "invoice attached" in blob
    assert "spam from a stranger" not in blob  # stranger chat never admitted


def test_whatsapp_lane_absent_store_is_noop(tmp_path: Path):
    out = tmp_path / "events.jsonl"
    res = sync_mod.run_sync(
        lanes=["whatsapp"],
        paths={"whatsapp": tmp_path / "nope.sqlite"},
        allowlist=_allowlist(),
        store_path=out,
        dry_run=False,
    )
    [lane] = res.lanes
    assert lane.read == 0
    assert res.written == 0
    assert not out.exists()


def test_lane_with_unavailable_adapter_is_skipped(tmp_path: Path, monkeypatch):
    # If an adapter factory raises LocalAdapterUnavailable, run_sync records the
    # lane as skipped and writes nothing for it — never crashes.
    import ingest.local as pkg

    def _boom(_path=None, **_kw):
        raise pkg.LocalAdapterUnavailable("simulated missing module")

    monkeypatch.setitem(pkg._LANE_FACTORIES, "whatsapp", _boom)
    # run_sync resolves whatsapp via its own helper which catches the error.
    monkeypatch.setattr(sync_mod, "whatsapp_adapter", _boom)
    out = tmp_path / "events.jsonl"
    res = sync_mod.run_sync(
        lanes=["whatsapp"],
        paths={},
        allowlist=_allowlist(),
        store_path=out,
        dry_run=True,
    )
    [lane] = res.lanes
    assert lane.skipped_reason == "adapter_unavailable"
    assert res.written == 0


# --------------------------------------------------------------------------- #
# Correspondents source loader                                                 #
# --------------------------------------------------------------------------- #


def test_load_correspondents_reads_list_and_skips_comments(tmp_path: Path):
    p = tmp_path / "sent-correspondents.txt"
    p.write_text("# my correspondents\n alice@example.com \n+1 (415) 555-0101, bob@example.com\n",
                 encoding="utf-8")
    got = sync_mod._load_correspondents(p)
    assert "alice@example.com" in got
    assert "+1 (415) 555-0101" in got
    assert "bob@example.com" in got
    assert "# my correspondents" not in got


def test_load_correspondents_missing_file_is_empty(tmp_path: Path):
    assert sync_mod._load_correspondents(tmp_path / "no-such.txt") == []


# --------------------------------------------------------------------------- #
# CLI entrypoint                                                               #
# --------------------------------------------------------------------------- #


def test_cli_dry_run_absent_stores(tmp_path: Path, capsys, monkeypatch):
    # `python -m ingest.local.sync --dry-run` over a fresh brain root with no
    # correspondents = clean no-op, exit 0, writes nothing.
    monkeypatch.setenv("MCS_BRAIN_ROOT", str(tmp_path / "brain"))
    monkeypatch.delenv("MCS_CORRESPONDENTS", raising=False)
    import mcs_paths

    mcs_paths.reset_cache()
    rc = sync_mod.main(["--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert not (tmp_path / "brain" / "local" / "events.jsonl").exists()


def test_cli_writes_with_explicit_paths_and_correspondents(tmp_path: Path, capsys, monkeypatch):
    db = tmp_path / "chat.db"
    make_chat_db(
        db,
        [{"chat_identifier": _FRIEND_PHONE, "handle": _FRIEND_PHONE,
          "text": "see you friday", "iso": "2026-06-20T10:00:00Z"}],
    )
    corr = tmp_path / "corr.txt"
    corr.write_text(_FRIEND_PHONE + "\n", encoding="utf-8")
    store = tmp_path / "events.jsonl"
    monkeypatch.setattr("ingest.allowlist.resolve_contacts_from_addressbook", lambda *a, **k: {})
    rc = sync_mod.main(
        ["--lane", "imessage", "--imessage-db", str(db),
         "--correspondents", str(corr), "--store", str(store)]
    )
    assert rc == 0
    assert store.is_file()
    rows = [l for l in store.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1
    assert "see you friday" in rows[0]
