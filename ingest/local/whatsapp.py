"""WhatsAppAdapter — read the founder's WhatsApp Desktop LOCAL message store.

WhatsApp is one of only two sources that genuinely needs *local* code (master
spec §6, mirrored in ``ingest/local/__init__.py``): a personal WhatsApp account
has no usable cloud API — the history lives in a SQLite store on the founder's
own Mac, behind the OS Full Disk Access privacy boundary. Real operation reads
the user's ACTUAL WhatsApp Desktop data on disk (see "Where the store lives"
below); there is no cloud round-trip and no WhatsApp login. This adapter is the
WhatsApp half of the thin **Mac sync agent**.

De-welded from the live company brain's ``imessage-ingest.py`` (its
``process_whatsapp`` and the helpers ``load_whatsapp_contacts`` /
``whatsapp_group_participants`` / ``whatsapp_participant_from_fields`` /
``whatsapp_contact_for_jid``). What carried over is the genuinely reusable spine —
the real WhatsApp Desktop schema subset and how to read it:

  * ``ChatStorage.sqlite`` (the messages DB):
      - ``ZWACHATSESSION``  one row per chat. ``ZCONTACTJID`` is the chat's JID;
        a GROUP chat's ends ``@g.us``, a 1:1's is the partner's JID.
        ``ZPARTNERNAME`` is the chat's display name.
      - ``ZWAMESSAGE``      one row per message. ``ZTEXT`` is the body,
        ``ZMESSAGEDATE`` an Apple-epoch timestamp (Core-Data SECONDS),
        ``ZISFROMME`` 1 if the founder sent it, ``ZFROMJID`` / ``ZTOJID`` the
        endpoints, ``ZGROUPMEMBER`` -> the group-member row that sent a group msg.
      - ``ZWAGROUPMEMBER``  one row per (group chat, member). ``ZMEMBERJID`` is
        the member's JID; ``ZCONTACTNAME`` / ``ZFIRSTNAME`` are name fallbacks.
  * ``ContactsV2.sqlite`` (the contact map, OPTIONAL):
      - ``ZWAADDRESSBOOKCONTACT``  maps a JID/LID to a real person:
        ``ZWHATSAPPID`` / ``ZLID`` (keys), ``ZPHONENUMBER`` /
        ``ZLOCALIZEDPHONENUMBER``, ``ZFULLNAME``, ``ZUSERNAME``.

What was DELETED (company-specific, not reusable): the YAML allowlist policy, the
``pillars/2-people`` slug roster, the Drive/manifest artifact writes, the
attachment/media inventory + iCloud-placeholder policy, the country-prefix
widening, and the bespoke 2FA/financial privacy regex (the generic ``mcs_egress``
gate replaces it).

Read by COLUMN NAME, never Core-Data positional ``Z_PK`` numbers (which drift
across WhatsApp versions). Read-only: each store is opened ``mode=ro&immutable=1``
so the live WhatsApp data can never be mutated and a live store's WAL is untouched.
The only I/O is reading the SQLite files you point it at. No network. A missing
store (or no Full Disk Access) yields nothing — the lane gracefully skips.

----------------------------------------------------------------------------
Two modes (one adapter, two contracts — see ``ingest/local/sync.py`` and the
``ingest/allowlist.py`` lane contract)
----------------------------------------------------------------------------
The local-lane SPINE (``sync.py``) treats every adapter as a DUMB READER: it pulls
all raw records (each carrying ``chat_id`` / ``chat_name``), then the spine applies
the shared identity ``Allowlist`` (``ingest/allowlist.py``) and the sanitize/dedup
pipeline. So by default (``allowlist=None``) this adapter reads everything and
filters nothing — the spine owns the gate.

The shared lane CONTRACT (``ingest/allowlist.py``) instead lets a lane CONSUME an
injected identity ``Allowlist`` and drop non-corresponded people itself. So when an
``Allowlist`` IS injected, this adapter additionally: resolves each JID through
``ContactsV2`` (and its own numeric user-part) into real identities, handles group
vs 1:1, DROPS any chat with no allowlisted participant AND any message whose
resolved sender is not allowlisted (and isn't the founder), and egress-sanitizes
each body — yielding only admissible records (and, via ``read_events``, genesis
``Event`` objects).

THE ALLOWLIST SEAM (filtered mode). A WhatsApp JID like
``14155550199@s.whatsapp.net`` is email-SHAPED, so handing it raw to
``Allowlist.contains`` would misread it as an email and never match the allowlisted
phone ``4155550199``. So a participant's JID is first resolved — via ContactsV2 and
the JID's numeric user-part — into its REAL identities (phone, username, name), and
THOSE are tested. This mirrors the live lane, which resolves the participant before
matching, never the bare JID. Injected-but-empty allowlist == fail-closed (admit
nobody), matching the live lane's shared identity ``Allowlist``.

----------------------------------------------------------------------------
Where the store lives (macOS, real operation)
----------------------------------------------------------------------------
WhatsApp Desktop keeps its local store under the app's group container::

    ~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite
    ~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ContactsV2.sqlite

Both are resolved through ``mcs_paths`` (config keys ``whatsapp_db`` /
``whatsapp_contacts_db``) and are fully overridable (``store_path=`` /
``contacts_db_path=`` for tests). Reading them on a real Mac requires the running
process to have **Full Disk Access** — an un-scriptable, one-time manual grant in
System Settings -> Privacy & Security -> Full Disk Access. Without it the open
fails and the adapter yields nothing. The tests run against a SYNTHETIC fixture
recreating the schema subset above, so the parse/map/filter logic is verified
without touching real data or needing the grant.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import mcs_paths  # noqa: E402
from mcs_egress import EgressGate  # noqa: E402

from ingest.allowlist import Allowlist  # noqa: E402

# WhatsApp's ZMESSAGEDATE is Core-Data seconds since 2001-01-01 — reuse the
# iMessage converter so both local lanes share one timestamp rule (DRY; the
# sibling already owns the magnitude-detecting conversion).
from ingest.local.imessage import _apple_date_to_iso  # noqa: E402

# Config keys for the two store files (overridable via mcs_paths resolution).
_CFG_DB = "whatsapp_db"
_CFG_CONTACTS_DB = "whatsapp_contacts_db"

# The real default location of WhatsApp Desktop's group container (see docstring).
_DEFAULT_GROUP_CONTAINER = "~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared"
_DEFAULT_DB_NAME = "ChatStorage.sqlite"
_DEFAULT_CONTACTS_DB_NAME = "ContactsV2.sqlite"


class WhatsAppAdapter:
    """Yield raw records (and, via ``read_events``, Events) from WhatsApp Desktop.

    Args:
        store_path: path to ``ChatStorage.sqlite``. Default: ``mcs_paths`` config
            key ``whatsapp_db``, else the real group-container location. A missing
            file yields nothing. (First positional arg so the ``ingest.local``
            registry's ``WhatsAppAdapter(store_path)`` construction keeps working.)
        allowlist: OPTIONAL injected identity ``Allowlist``. ``None`` (the spine's
            default) -> DUMB READER: read everything, filter nothing (the sync
            spine applies its own allowlist + sanitize). A passed ``Allowlist`` ->
            FILTERED mode: resolve JIDs, drop non-allowlisted chats/senders, and
            egress-sanitize bodies in-adapter. An empty ``Allowlist`` is
            fail-closed (admits nobody).
        contacts_db_path: path to ``ContactsV2.sqlite`` (used in filtered mode for
            JID -> name/phone). Default: config key ``whatsapp_contacts_db``, else
            the group-container location. May be absent.
        kind: the genesis ``kind`` stamped on every record.
        gate: the ``EgressGate`` used in filtered mode to drop private bodies.
        self_handle: the label used for the founder's own (``ZISFROMME``) messages.
    """

    def __init__(
        self,
        store_path: str | os.PathLike[str] | None = None,
        *,
        allowlist: Allowlist | None = None,
        contacts_db_path: str | os.PathLike[str] | None = None,
        kind: str = "whatsapp",
        gate: EgressGate | None = None,
        self_handle: str = "self",
    ):
        self.store_path = _resolve_store_path(store_path, _CFG_DB, _DEFAULT_DB_NAME)
        self.contacts_db_path = _resolve_store_path(
            contacts_db_path, _CFG_CONTACTS_DB, _DEFAULT_CONTACTS_DB_NAME
        )
        self.allowlist = allowlist            # None => dumb-reader mode
        self.kind = kind
        self.gate = gate or EgressGate()
        self.self_handle = self_handle

    # -- public API ------------------------------------------------------- #

    def read(self) -> Iterator[dict[str, Any]]:
        """Yield one RAW RECORD dict per message (the adapter contract shared with
        the email + local-file + iMessage lanes).

        Dumb-reader mode (no allowlist): every message, unfiltered. Filtered mode
        (allowlist injected): only allowlist-admitted, egress-sanitized messages.
        """
        if not self.store_path.is_file():
            return  # store not present (or no Full Disk Access) -> nothing
        conn = _connect_ro(self.store_path)
        if conn is None:
            return
        try:
            conn.row_factory = sqlite3.Row
            if not (_table_exists(conn, "ZWACHATSESSION") and _table_exists(conn, "ZWAMESSAGE")):
                return  # not a usable WhatsApp store -> skip the lane
            contacts = self._load_contacts() if self.allowlist is not None else {}
            yield from self._read_messages(conn, contacts)
        finally:
            conn.close()

    def read_events(self) -> Iterator[Any]:
        """Yield genesis ``Event`` objects directly (filtered + egress-sanitized
        when an allowlist is injected), for callers that want Events without the
        generic pipeline. The normalizer import is deferred to keep this module
        import-light; the Events match what ``ingest_records`` builds from
        ``read()``.
        """
        from ingest.normalize import normalize_record

        for record in self.read():
            yield normalize_record(record)

    # -- internals -------------------------------------------------------- #

    def _read_messages(
        self, conn: sqlite3.Connection, contacts: dict[str, dict[str, str]]
    ) -> Iterator[dict[str, Any]]:
        has_group_member = _table_exists(conn, "ZWAGROUPMEMBER")
        sessions = self._load_sessions(conn, contacts, has_group_member)
        if not sessions:
            return
        try:
            cursor = conn.execute(_message_query(sessions, has_group_member))
        except sqlite3.DatabaseError:
            return
        for row in cursor:
            record = self._record_from_row(row, sessions, contacts)
            if record is not None:
                yield record

    def _load_sessions(
        self,
        conn: sqlite3.Connection,
        contacts: dict[str, dict[str, str]],
        has_group_member: bool,
    ) -> dict[int, dict[str, Any]]:
        """Build the per-session metadata. In filtered mode a session is INCLUDED
        only if at least one participant is allowlisted; in dumb-reader mode every
        (non-removed) session is included."""
        removed_clause = _removed_clause(conn)
        try:
            rows = conn.execute(
                f"SELECT Z_PK, ZCONTACTJID, ZPARTNERNAME FROM ZWACHATSESSION{removed_clause} ORDER BY Z_PK"
            ).fetchall()
        except sqlite3.DatabaseError:
            return {}

        sessions: dict[int, dict[str, Any]] = {}
        for row in rows:
            session_id = row["Z_PK"]
            if session_id is None:
                continue
            session_id = int(session_id)
            contact_jid = _str(row["ZCONTACTJID"])
            partner_name = _str(row["ZPARTNERNAME"])
            is_group = contact_jid.endswith("@g.us")

            # Participants are only resolved when filtering (the dumb reader doesn't
            # need ContactsV2 or group membership to pass a row through).
            participants: list[dict[str, str]] = []
            if self.allowlist is not None:
                if is_group:
                    participants = (
                        self._group_participants(conn, contacts, session_id)
                        if has_group_member
                        else []
                    )
                else:
                    participants = [
                        _participant(jid=contact_jid, contacts=contacts, fallback_name=partner_name)
                    ]
                if not any(self._participant_admitted(p) for p in participants):
                    continue  # no allowlisted participant -> drop the whole chat

            counterparty_labels = [
                _participant_label(p) for p in participants if _participant_label(p)
            ]
            display_name = partner_name or ", ".join(counterparty_labels) or contact_jid
            sessions[session_id] = {
                "contact_jid": contact_jid,
                "is_group": is_group,
                "display_name": display_name,
                # 1:1 counterparty name is the human fallback for a message whose
                # own row carries no sender name.
                "counterparty_name": (counterparty_labels[0] if (not is_group and counterparty_labels) else ""),
                "participant_handles": _participant_handles(participants, self.self_handle),
            }
        return sessions

    def _group_participants(
        self, conn: sqlite3.Connection, contacts: dict[str, dict[str, str]], session_id: int
    ) -> list[dict[str, str]]:
        try:
            rows = conn.execute(
                """
                SELECT ZMEMBERJID, ZCONTACTNAME, ZFIRSTNAME
                FROM ZWAGROUPMEMBER
                WHERE ZCHATSESSION = ?
                ORDER BY Z_PK
                """,
                (session_id,),
            ).fetchall()
        except sqlite3.DatabaseError:
            return []
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for member_jid, contact_name, first_name in rows:
            participant = _participant(
                jid=member_jid,
                contacts=contacts,
                fallback_name=contact_name,
                fallback_username=first_name,
            )
            key = participant["jid"] or participant["phone"] or participant["full_name"]
            if key and key not in seen:
                seen.add(key)
                out.append(participant)
        return out

    def _record_from_row(
        self,
        row: sqlite3.Row,
        sessions: dict[int, dict[str, Any]],
        contacts: dict[str, dict[str, str]],
    ) -> dict[str, Any] | None:
        session_id = row["session_id"]
        if session_id is None:
            return None
        session = sessions.get(int(session_id))
        if session is None:
            return None
        text = _str(row["text"])

        sender_handle = self.self_handle
        if self.allowlist is not None:
            # Filtered mode: an empty body has nothing to ingest (egress would
            # reject it too); a non-allowlisted speaker is dropped; the body is
            # egress-screened.
            if not text:
                return None
            if not bool(row["is_from_me"]):
                sender = _participant(
                    jid=_str(row["member_jid"]) or _str(row["from_jid"]) or _str(row["to_jid"]),
                    contacts=contacts,
                    # group messages carry the sender name on their own row; a 1:1
                    # message does not, so fall back to the chat's counterparty name.
                    fallback_name=_str(row["member_name"]) or session.get("counterparty_name", ""),
                    fallback_username=_str(row["member_first_name"]),
                )
                if not self._participant_admitted(sender):
                    return None  # a non-allowlisted speaker in an admitted chat
                sender_handle = _participant_label(sender)
            if self.gate.classify(text) == "private":
                return None  # never emit a private/unclassifiable body
        else:
            # Dumb-reader mode: surface every row (the spine sanitizes downstream).
            sender_handle = (
                self.self_handle
                if bool(row["is_from_me"])
                else (_str(row["from_jid"]) or _str(row["contact_jid"]))
            )

        message_id = row["message_id"]
        chat_id = session["contact_jid"] or _str(row["contact_jid"])
        is_from_me = bool(row["is_from_me"])
        return {
            "kind": self.kind,
            "source_type": self.kind,
            # Stable per-message id: WhatsApp's Z_PK is stable within one store;
            # scope it with ``wa-`` so it never collides with an iMessage rowid.
            "source_id": f"wa-{int(message_id)}" if message_id is not None else "",
            "chat_id": chat_id,
            "chat_name": session["display_name"] or chat_id,
            "locator": "",
            "text": text,
            "title": session["display_name"] or chat_id,
            "observed_at": _apple_date_to_iso(row["date"]),
            "is_from_me": is_from_me,
            "participants": session["participant_handles"] if self.allowlist is not None
                            else [p for p in (_str(row["from_jid"]),) if p],
            "meta": {
                "adapter": "whatsapp",
                "is_group": session["is_group"],
                "is_from_me": is_from_me,
                "sender": sender_handle,
                "chat_id": chat_id,
            },
        }

    # -- allowlist application (filtered mode only) ---------------------- #

    def _participant_admitted(self, participant: dict[str, str]) -> bool:
        """True iff ANY of the participant's RESOLVED identities is allowlisted.

        The JID is resolved into real identities (phone / username / name) before
        testing — never the raw ``user@host`` JID, which the allowlist would
        misread as an email.
        """
        if self.allowlist is None:
            return True
        return any(self.allowlist.contains(idn) for idn in _participant_identities(participant))

    def _load_contacts(self) -> dict[str, dict[str, str]]:
        """Build the JID/LID -> contact map from ContactsV2.sqlite. Best-effort: a
        missing/unreadable contacts DB (or one lacking the table) yields {}."""
        if not self.contacts_db_path.is_file():
            return {}
        conn = _connect_ro(self.contacts_db_path)
        if conn is None:
            return {}
        try:
            if not _table_exists(conn, "ZWAADDRESSBOOKCONTACT"):
                return {}
            try:
                rows = conn.execute(
                    """
                    SELECT ZWHATSAPPID, ZLID, ZPHONENUMBER,
                           ZLOCALIZEDPHONENUMBER, ZFULLNAME, ZUSERNAME
                    FROM ZWAADDRESSBOOKCONTACT
                    """
                ).fetchall()
            except sqlite3.DatabaseError:
                return {}
            contacts: dict[str, dict[str, str]] = {}
            for wid, lid, phone, localized, full_name, username in rows:
                contact = {
                    "phone": _str(phone),
                    "localized_phone": _str(localized),
                    "full_name": _str(full_name),
                    "username": _str(username),
                }
                for key in (_str(wid), _str(lid)):
                    if key:
                        contacts[key] = contact
            return contacts
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# SQL builders                                                                 #
# --------------------------------------------------------------------------- #


def _removed_clause(conn: sqlite3.Connection) -> str:
    """``WHERE`` clause excluding removed sessions, only if the column exists (the
    minimal synthetic fixtures may omit ``ZREMOVED``)."""
    if _column_exists(conn, "ZWACHATSESSION", "ZREMOVED"):
        return " WHERE ZREMOVED = 0 OR ZREMOVED IS NULL"
    return ""


def _message_query(sessions: dict[int, dict[str, Any]], has_group_member: bool) -> str:
    """Build the message SELECT for the included sessions. Read by COLUMN NAME;
    LEFT JOIN ZWAGROUPMEMBER so a group message's sender resolves (member columns
    are NULL when that table is absent)."""
    join = "LEFT JOIN ZWAGROUPMEMBER gm ON gm.Z_PK = m.ZGROUPMEMBER" if has_group_member else ""
    gm_cols = (
        "gm.ZMEMBERJID AS member_jid, gm.ZCONTACTNAME AS member_name, gm.ZFIRSTNAME AS member_first_name"
        if has_group_member
        else "NULL AS member_jid, NULL AS member_name, NULL AS member_first_name"
    )
    ids = ",".join(str(int(s)) for s in sorted(sessions))
    return f"""
        SELECT
            m.Z_PK          AS message_id,
            m.ZCHATSESSION  AS session_id,
            m.ZMESSAGEDATE  AS date,
            m.ZISFROMME     AS is_from_me,
            m.ZTEXT         AS text,
            m.ZFROMJID      AS from_jid,
            m.ZTOJID        AS to_jid,
            s.ZCONTACTJID   AS contact_jid,
            {gm_cols}
        FROM ZWAMESSAGE m
        LEFT JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION
        {join}
        WHERE m.ZCHATSESSION IN ({ids})
        ORDER BY m.ZMESSAGEDATE ASC, m.Z_PK ASC
    """


# --------------------------------------------------------------------------- #
# Participant identity helpers (the JID -> real-identity resolution)            #
# --------------------------------------------------------------------------- #


def _participant(
    *,
    jid: str | None,
    contacts: dict[str, dict[str, str]],
    fallback_name: str | None = None,
    fallback_username: str | None = None,
) -> dict[str, str]:
    """Resolve a JID to a participant dict using the contacts map, with name /
    username fallbacks from the message / group rows when the contact is unknown."""
    contact = _contact_for_jid(contacts, jid)
    return {
        "jid": _str(jid),
        "phone": _str(contact.get("phone")),
        "localized_phone": _str(contact.get("localized_phone")),
        "full_name": _str(contact.get("full_name")) or _str(fallback_name),
        "username": _str(contact.get("username")) or _str(fallback_username),
    }


def _contact_for_jid(
    contacts: dict[str, dict[str, str]], jid: str | None
) -> dict[str, str]:
    """Look a JID up in the contacts map, trying the raw JID, its user-part, and
    the user-part re-suffixed as ``@lid`` / ``@s.whatsapp.net`` (the live lane's
    candidate set)."""
    raw = _str(jid)
    if not raw:
        return {}
    base = raw.split("@", 1)[0]
    for candidate in (raw, base, f"{base}@lid", f"{base}@s.whatsapp.net"):
        if candidate in contacts:
            return contacts[candidate]
    return {}


def _participant_identities(participant: dict[str, str]) -> list[str]:
    """The identities to test against the allowlist for one participant.

    Crucially this does NOT include the raw ``user@host`` JID (the allowlist would
    read it as an email). It includes: the contact phone(s); the JID's user-part
    when numeric (that IS the phone for a ``...@s.whatsapp.net`` JID); the
    username; and the contact / display name.
    """
    out: list[str] = []
    for key in ("phone", "localized_phone", "username", "full_name"):
        value = participant.get(key)
        if value:
            out.append(value)
    base = participant.get("jid", "").split("@", 1)[0]
    if base and base.lstrip("+").isdigit():
        out.append(base)  # the numeric user-part of a phone-JID IS the phone
    return out


def _participant_label(participant: dict[str, str]) -> str:
    """A stable, non-raw-JID label for a participant (name > username > phone >
    JID user-part). Used as the sanitized sender handle in filtered mode."""
    for key in ("full_name", "username", "phone", "localized_phone"):
        value = participant.get(key)
        if value:
            return value
    base = participant.get("jid", "").split("@", 1)[0]
    return base or "unknown"


def _participant_handles(participants: Iterable[dict[str, str]], self_handle: str) -> list[str]:
    """The chat's participant handle list (founder first, then each resolved
    counterparty label), de-duplicated, order-stable."""
    out: list[str] = [self_handle]
    seen = {self_handle}
    for participant in participants:
        label = _participant_label(participant)
        if label and label not in seen:
            seen.add(label)
            out.append(label)
    return out


# --------------------------------------------------------------------------- #
# Low-level helpers                                                             #
# --------------------------------------------------------------------------- #


def _resolve_store_path(
    explicit: str | os.PathLike[str] | None, config_key: str, default_name: str
) -> Path:
    """Resolve a store file path: explicit arg > mcs config key > the real
    WhatsApp Desktop group-container location. Never required to exist."""
    if explicit is not None:
        return mcs_paths._norm(explicit)
    cfg = mcs_paths._read_config().get(config_key)
    if cfg:
        return mcs_paths._norm(cfg)
    return mcs_paths._norm(f"{_DEFAULT_GROUP_CONTAINER}/{default_name}")


def _connect_ro(path: Path) -> sqlite3.Connection | None:
    """Open the SQLite file strictly read-only (URI ``mode=ro&immutable=1``).

    Read-only is a hard guarantee: the live WhatsApp store must never be mutated
    by the brain, and ``immutable=1`` avoids touching a live store's WAL. Returns
    None if the file can't be opened (e.g. a present-but-garbage non-SQLite file).
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error:
        return None
    # Touch the schema so a present-but-garbage file fails HERE (caught), not mid-iter.
    try:
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
    except sqlite3.DatabaseError:
        conn.close()
        return None
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
        ).fetchone()
    except sqlite3.DatabaseError:
        return False
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.DatabaseError:
        return False
    return any((c[1] if not isinstance(c, sqlite3.Row) else c["name"]) == column for c in cols)


def _str(value: Any) -> str:
    """Collapse a possibly-None DB value to a stripped string ('' for None)."""
    return str(value).strip() if value is not None else ""
