"""END-TO-END test of the local-lane wiring, on SYNTHETIC fixtures only.

This is the integration proof the other local tests don't give in one place: the
WHOLE chain, from the email lane that DEFINES the correspondent set through to the
on-device message lanes that CONSUME it — wired exactly as ``ingest.local.sync``
(and ``run.py``) wire it. It asserts, with explicit checks, the four behaviors the
lane contract turns on:

  (a) an inbound EMAIL from a NON-correspondent is DROPPED; from a correspondent
      is KEPT (the SENT-folder spam filter);
  (b) the shared ``Allowlist`` is BUILT from the SENT correspondents (the email
      lane is the producer);
  (c) an iMessage / WhatsApp from an ALLOWLISTED person is INGESTED; from a
      NON-allowlisted person is DROPPED (the message lanes are the consumers);
  (d) a planted SECRET is sanitized away before it can become an Event.

Why synthetic-only (and honestly so): the real macOS ``chat.db`` and WhatsApp
``ChatStorage.sqlite`` live behind **Full Disk Access** — a one-time manual grant
in System Settings that NO script can perform (that's the point of the wall). So
the real stores are NOT touched here; instead each fixture recreates the exact
schema SUBSET the adapters read, proving the parse → allowlist → sanitize → Event
logic without ever needing the grant or any private data. The grant is documented
in ``docs/INGEST-ARCHITECTURE.md`` as the one un-automatable step.

GATED-FOR-OPERATOR (documented, not done here): pointing the lanes at the REAL
stores on this Mac needs that Full Disk Access grant — an operator action. This
suite proves the code against fixtures matching the real schema; the live run is
the operator's to enable.

Run (the repo convention — bust the Apple bytecode cache, not just __pycache__):
    rm -rf ~/Library/Caches/com.apple.python 2>/dev/null; \\
    /usr/bin/python3 -B -m pytest -q
"""

from __future__ import annotations

import mailbox
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ingest.allowlist import Allowlist, build_allowlist
from ingest.adapters import EmailAdapter
from ingest.local.sync import build_allowlist_from_email, iter_local_records, run_sync
from ingest.local.whatsapp import WhatsAppAdapter
from ingest.pipeline import ingest_records

# --------------------------------------------------------------------------- #
# The cast (no real people): one correspondent the founder has emailed, one    #
# stranger the founder never wrote to. The same human is reachable across      #
# lanes — email, a phone (iMessage handle), and a WhatsApp JID.                #
# --------------------------------------------------------------------------- #

FOUNDER = "founder@my-co.example"
FRIEND_EMAIL = "alice@partner.example"
FRIEND_PHONE = "+14155550101"                       # iMessage handle / contact phone
FRIEND_WA_JID = "14155550101@s.whatsapp.net"        # WhatsApp JID (numeric == phone)
STRANGER_EMAIL = "deals@spam-newsletter.example"
STRANGER_PHONE = "+19998887777"
STRANGER_WA_JID = "19998887777@s.whatsapp.net"

# The contacts bridge the email lane uses on a real Mac (Contacts read): the
# friend emailed at FRIEND_EMAIL also reaches the founder from FRIEND_PHONE. We
# inject it explicitly so the test is pure (no macOS Contacts read).
CONTACTS_BRIDGE = {FRIEND_EMAIL: [FRIEND_PHONE]}

_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Synthetic fixture builders — each recreates the real schema SUBSET           #
# --------------------------------------------------------------------------- #


def _apple_ns(iso: str) -> int:
    """A modern ``chat.db`` ``message.date``: NANOSECONDS since the Apple epoch."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int((dt - _APPLE_EPOCH).total_seconds() * 1_000_000_000)


def _apple_s(iso: str) -> float:
    """A WhatsApp ``ZMESSAGEDATE``: SECONDS since the Apple epoch."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (dt - _APPLE_EPOCH).total_seconds()


# Reusable .eml message templates --------------------------------------------- #

def _sent_eml(to_addr: str, subject: str, body: str) -> str:
    """A message the FOUNDER sent (From == founder, with the Gmail Sent label) —
    its To/Cc seed the correspondent set."""
    return (
        f"From: Founder <{FOUNDER}>\r\n"
        f"To: <{to_addr}>\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <sent-{subject.replace(' ', '')}@my-co.example>\r\n"
        "Date: Fri, 19 Jun 2026 09:00:00 +0000\r\n"
        "X-Gmail-Labels: Sent,Important\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n\r\n'
        f"{body}\r\n"
    )


def _inbound_eml(from_addr: str, subject: str, body: str, msgid: str) -> str:
    """An INBOUND message (From == someone else, To the founder)."""
    return (
        f"From: <{from_addr}>\r\n"
        f"To: Founder <{FOUNDER}>\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <{msgid}>\r\n"
        "Date: Sat, 20 Jun 2026 10:00:00 +0000\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n\r\n'
        f"{body}\r\n"
    )


def make_mbox_with_sent(path: Path) -> None:
    """A single mbox carrying the founder's SENT mail + inbound from both a
    correspondent and a stranger (the flat-mbox shape, where the ``Sent`` Gmail
    label marks the sent folder)."""
    box = mailbox.mbox(str(path))
    box.lock()
    try:
        # SENT: the founder emailed the friend -> friend becomes a correspondent.
        box.add(_sent_eml(FRIEND_EMAIL, "kickoff", "Great to connect, lets go."))
        # INBOUND from the correspondent -> KEPT.
        box.add(_inbound_eml(
            FRIEND_EMAIL, "Q4 pricing",
            "list_price = 5200, locking the first deal.", "in-friend@partner.example",
        ))
        # INBOUND from a stranger the founder never wrote to -> DROPPED.
        box.add(_inbound_eml(
            STRANGER_EMAIL, "80% OFF EVERYTHING",
            "Click here to claim your prize.", "in-spam@spam-newsletter.example",
        ))
        box.flush()
    finally:
        box.unlock()
        box.close()


def make_chat_db(path: Path, rows: list[dict]) -> None:
    """Synthetic iMessage ``chat.db`` (the real table subset the adapter reads).

    Each row: {chat_identifier, handle, text, iso, is_from_me?, display_name?}.
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


def make_whatsapp_store(path: Path, sessions: list[dict], messages: list[dict]) -> None:
    """Synthetic WhatsApp ``ChatStorage.sqlite`` (the schema the adapter queries).

    ``sessions``: {z_pk, contact_jid, partner_name?}.
    ``messages``: {z_pk, session, iso, text, is_from_me?, from_jid?}.
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
# (a) + (b): the EMAIL lane — drops non-correspondents, keeps correspondents,  #
#            and BUILDS the allowlist from the SENT correspondents.            #
# --------------------------------------------------------------------------- #


def test_email_lane_filters_inbound_and_seeds_allowlist(tmp_path: Path):
    mbox = tmp_path / "all.mbox"
    make_mbox_with_sent(mbox)

    email = EmailAdapter(path=mbox, user_email=FOUNDER)
    records = email.build()

    # (a) inbound from the correspondent is KEPT; from the stranger is DROPPED.
    kept_ids = sorted(r["source_id"] for r in records)
    assert kept_ids == ["in-friend@partner.example"]          # friend kept
    bodies = " ".join(r["text"] for r in records)
    assert "list_price = 5200" in bodies                       # the friend's body
    assert "claim your prize" not in bodies                    # spam never ingested

    # (b) the correspondent set is harvested from the SENT folder's To/Cc, and
    # the shared allowlist is BUILT from it (the email lane is the producer).
    assert email.sent_correspondents == {FRIEND_EMAIL}
    assert STRANGER_EMAIL not in email.sent_correspondents

    allowlist = build_allowlist_from_email(email, contacts=CONTACTS_BRIDGE)
    assert isinstance(allowlist, Allowlist)
    assert allowlist.contains(FRIEND_EMAIL)        # the correspondent
    assert allowlist.contains(FRIEND_PHONE)        # bridged via Contacts -> his phone
    assert not allowlist.contains(STRANGER_EMAIL)  # a non-correspondent never matches
    assert not allowlist.contains(STRANGER_PHONE)


def test_build_allowlist_from_email_empty_when_no_sent(tmp_path: Path):
    # No SENT mail discoverable -> no correspondents -> an EMPTY allowlist, which
    # the message lanes treat as fail-closed (admit nobody). Not an error.
    mbox = tmp_path / "inbound-only.mbox"
    box = mailbox.mbox(str(mbox))
    box.lock()
    try:
        box.add(_inbound_eml(FRIEND_EMAIL, "hi", "no sent mail exists here", "x@partner.example"))
        box.flush()
    finally:
        box.unlock()
        box.close()

    allowlist = build_allowlist_from_email(EmailAdapter(path=mbox, user_email=FOUNDER), contacts={})
    assert len(allowlist) == 0
    assert not allowlist  # falsy == admits nobody


# --------------------------------------------------------------------------- #
# (c): the MESSAGE lanes consume that allowlist — allowlisted in, stranger out. #
#      Proven on BOTH iMessage and WhatsApp, through the same shared seam.      #
# --------------------------------------------------------------------------- #


def _allowlist_from_email_mbox(tmp_path: Path) -> Allowlist:
    """The real producer→consumer handoff: build the allowlist the message lanes
    will consume FROM the email lane (not hand-rolled), bridging the friend's
    email to his phone the way a Mac's Contacts read would."""
    mbox = tmp_path / "all.mbox"
    make_mbox_with_sent(mbox)
    return build_allowlist_from_email(
        EmailAdapter(path=mbox, user_email=FOUNDER), contacts=CONTACTS_BRIDGE
    )


def test_imessage_lane_consumes_email_allowlist(tmp_path: Path):
    allowlist = _allowlist_from_email_mbox(tmp_path)
    db = tmp_path / "chat.db"
    make_chat_db(
        db,
        [
            # the correspondent (matched by phone, bridged from his email) -> KEPT
            {"chat_identifier": FRIEND_PHONE, "handle": FRIEND_PHONE, "display_name": "Alice",
             "text": "are we still on for friday?", "iso": "2026-06-20T10:00:00Z"},
            # the stranger -> DROPPED before the body is even kept
            {"chat_identifier": STRANGER_PHONE, "handle": STRANGER_PHONE, "display_name": "Spam",
             "text": "limited time offer just for you", "iso": "2026-06-20T11:00:00Z"},
        ],
    )

    records = list(iter_local_records(allowlist, paths={"imessage": db}, lanes=["imessage"]))
    texts = [r["text"] for r in records]
    assert "are we still on for friday?" in texts        # allowlisted -> in
    assert "limited time offer just for you" not in texts  # stranger -> out
    assert len(records) == 1


def test_whatsapp_lane_consumes_email_allowlist(tmp_path: Path):
    allowlist = _allowlist_from_email_mbox(tmp_path)
    store = tmp_path / "ChatStorage.sqlite"
    make_whatsapp_store(
        store,
        sessions=[
            {"z_pk": 1, "contact_jid": FRIEND_WA_JID, "partner_name": "Alice"},
            {"z_pk": 2, "contact_jid": STRANGER_WA_JID, "partner_name": "Spam"},
        ],
        messages=[
            # the correspondent (JID's numeric user-part == his allowlisted phone) -> KEPT
            {"z_pk": 10, "session": 1, "iso": "2026-06-19T09:00:00Z", "is_from_me": False,
             "text": "sending the deck now", "from_jid": FRIEND_WA_JID},
            # the stranger's whole chat -> DROPPED internally by the adapter
            {"z_pk": 11, "session": 2, "iso": "2026-06-19T10:00:00Z", "is_from_me": False,
             "text": "spam from a stranger", "from_jid": STRANGER_WA_JID},
        ],
    )

    records = list(iter_local_records(allowlist, paths={"whatsapp": store}, lanes=["whatsapp"]))
    texts = [r["text"] for r in records]
    assert "sending the deck now" in texts            # allowlisted -> in
    assert "spam from a stranger" not in texts        # stranger chat -> out
    assert len(records) == 1


# --------------------------------------------------------------------------- #
# (d): a planted SECRET is sanitized away before it can become an Event.       #
#      Proven through the FULL spine (iter_local_records -> ingest_records).    #
# --------------------------------------------------------------------------- #


def test_planted_secret_is_sanitized_before_becoming_an_event(tmp_path: Path):
    allowlist = _allowlist_from_email_mbox(tmp_path)
    db = tmp_path / "chat.db"
    make_chat_db(
        db,
        [
            # a harmless message from the correspondent -> becomes an Event
            {"chat_identifier": FRIEND_PHONE, "handle": FRIEND_PHONE, "display_name": "Alice",
             "text": "lunch at noon?", "iso": "2026-06-20T10:00:00Z"},
            # a SECRET from the SAME (allowlisted) correspondent -> must be dropped
            # by the egress gate, so it never becomes an Event.
            {"chat_identifier": FRIEND_PHONE, "handle": FRIEND_PHONE, "display_name": "Alice",
             "text": "the api_key = sk-ABCD1234ABCD1234ABCD",  # pragma: allowlist secret
             "iso": "2026-06-20T12:00:00Z"},
        ],
    )

    scoped = list(iter_local_records(allowlist, paths={"imessage": db}, lanes=["imessage"]))
    assert len(scoped) == 2  # the allowlist admits both (both from the correspondent)

    ingested = ingest_records(scoped)
    assert ingested.kept == 1            # only the harmless one survives
    assert ingested.dropped_private == 1  # the secret-bearing one is dropped

    # The secret appears in NO produced Event — anywhere.
    all_text = " ".join(ev.text for ev in ingested.corpus.all_events())
    assert "lunch at noon?" in all_text
    assert "sk-ABCD" not in all_text
    assert "api_key" not in all_text


# --------------------------------------------------------------------------- #
# The whole chain, through the real sync entry point (run_sync), in one go:    #
# email builds the allowlist -> both lanes ingest -> sanitized Events written. #
# --------------------------------------------------------------------------- #


def test_full_chain_through_run_sync_writes_only_sanitized_correspondent_events(tmp_path: Path):
    # PRODUCER: the email lane defines who the founder corresponds with.
    allowlist = _allowlist_from_email_mbox(tmp_path)

    # CONSUMERS: both on-device lanes, each with a correspondent + a stranger +
    # (iMessage) a planted secret from the correspondent.
    db = tmp_path / "chat.db"
    make_chat_db(
        db,
        [
            {"chat_identifier": FRIEND_PHONE, "handle": FRIEND_PHONE, "display_name": "Alice",
             "text": "launch_date = 2026-10-15", "iso": "2026-06-20T10:00:00Z"},
            {"chat_identifier": STRANGER_PHONE, "handle": STRANGER_PHONE,
             "text": "you do not know me", "iso": "2026-06-20T11:00:00Z"},
            {"chat_identifier": FRIEND_PHONE, "handle": FRIEND_PHONE, "display_name": "Alice",
             "text": "secret = sk-ZZZZ9999ZZZZ9999ZZZZ",  # pragma: allowlist secret
             "iso": "2026-06-20T12:00:00Z"},
        ],
    )
    store = tmp_path / "ChatStorage.sqlite"
    make_whatsapp_store(
        store,
        sessions=[
            {"z_pk": 1, "contact_jid": FRIEND_WA_JID, "partner_name": "Alice"},
            {"z_pk": 2, "contact_jid": STRANGER_WA_JID, "partner_name": "Spam"},
        ],
        messages=[
            {"z_pk": 10, "session": 1, "iso": "2026-06-19T09:00:00Z", "is_from_me": False,
             "text": "deck is ready", "from_jid": FRIEND_WA_JID},
            {"z_pk": 11, "session": 2, "iso": "2026-06-19T10:00:00Z", "is_from_me": False,
             "text": "stranger noise", "from_jid": STRANGER_WA_JID},
        ],
    )

    out = tmp_path / "events.jsonl"
    res = run_sync(
        paths={"imessage": db, "whatsapp": store},
        allowlist=allowlist,
        store_path=out,
        dry_run=False,
    )

    # Two correspondent events survive (one per lane); the stranger chats and the
    # secret are gone. iMessage: read 3, admitted 2 (stranger dropped), kept 1
    # (secret dropped). WhatsApp: the adapter dropped the stranger chat itself.
    assert res.total_kept == 2
    assert res.written == 2
    im = next(l for l in res.lanes if l.lane == "imessage")
    assert im.read == 3 and im.after_allowlist == 2 and im.kept == 1
    assert im.dropped_private == 1

    blob = out.read_text(encoding="utf-8")
    assert "launch_date = 2026-10-15" in blob   # correspondent iMessage kept
    assert "deck is ready" in blob              # correspondent WhatsApp kept
    assert "you do not know me" not in blob     # iMessage stranger dropped
    assert "stranger noise" not in blob         # WhatsApp stranger dropped
    assert "sk-ZZZZ" not in blob                # planted secret sanitized away


def test_run_sync_local_lanes_absent_stores_are_clean_noop(tmp_path: Path):
    # The graceful-absence guarantee (off-Mac / before Full Disk Access): absent
    # stores => nothing read, nothing written, no file created.
    allowlist = _allowlist_from_email_mbox(tmp_path)
    out = tmp_path / "events.jsonl"
    res = run_sync(
        paths={"imessage": tmp_path / "nope.db", "whatsapp": tmp_path / "nope.sqlite"},
        allowlist=allowlist,
        store_path=out,
        dry_run=False,
    )
    assert res.total_kept == 0
    assert res.written == 0
    assert not out.exists()
    for lane in res.lanes:
        assert lane.read == 0
