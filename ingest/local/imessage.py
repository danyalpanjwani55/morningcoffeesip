"""iMessageAdapter — read the local macOS Messages database (``chat.db``).

iMessage is one of only two sources that genuinely needs *local* code (master
spec §6): there is no cloud API for it — the messages live in a SQLite file on
the founder's own Mac, behind macOS **Full Disk Access** (a manual privacy grant
that cannot be scripted). This adapter is the iMessage half of the thin **Mac
sync agent**: it reads that file read-only and yields raw records the shared
ingest spine then allowlist-filters → sanitizes → dedups → normalizes into
genesis Events. It does the source-specific SQL parsing ONLY; it does not
sanitize, dedup, or build Events (the spine owns that — one place, reused).

The real schema subset we read (Apple's ``chat.db``, stable for years):

    message(ROWID, guid, text, attributedBody, date, is_from_me, handle_id)
    handle(ROWID, id)                     -- id = phone number or email
    chat(ROWID, guid, chat_identifier, display_name)
    chat_message_join(chat_id, message_id)

``message.date`` is **nanoseconds since the Apple epoch 2001-01-01 UTC** (older
rows are seconds; we detect the magnitude and convert either way). ``handle.id``
is the counterparty's phone/email; ``is_from_me`` marks the founder's own sends.
``chat_identifier`` is the per-conversation key the allowlist matches on.

Tested against a **synthetic SQLite fixture** that creates exactly these tables —
the real ``chat.db`` needs Full Disk Access (an un-scriptable manual grant), so
the code is exercised against a fixture matching the real schema subset, and the
grant is documented in ``docs/INGEST-ARCHITECTURE.md`` rather than faked.

Read-only: opened ``mode=ro`` via a URI so the adapter can never mutate the live
Messages store. The only I/O is reading the SQLite file you point it at. No
network. A missing DB yields nothing (the lane gracefully skips).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import mcs_paths  # noqa: E402

# Apple's Core Data / chat.db epoch: 2001-01-01 00:00:00 UTC.
_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
# Threshold to tell nanosecond timestamps (modern macOS) from second timestamps
# (older rows): anything past ~1e11 is nanoseconds, below is seconds.
_NS_THRESHOLD = 1_000_000_000_00

# The default location of the live Messages DB on macOS. Resolved only as a
# fallback default; tests always pass an explicit ``db_path=``.
_DEFAULT_CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"

# The single query that joins a message to its chat + handle. LEFT JOINs so a
# message with no handle (rare) or no chat still surfaces rather than vanishing.
_QUERY = """
SELECT
    m.ROWID            AS rowid,
    m.guid             AS guid,
    m.text             AS text,
    m.date             AS date,
    m.is_from_me       AS is_from_me,
    h.id               AS handle_id,
    c.chat_identifier  AS chat_identifier,
    c.display_name     AS display_name
FROM message m
LEFT JOIN handle h ON m.handle_id = h.ROWID
LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
LEFT JOIN chat c ON c.ROWID = cmj.chat_id
ORDER BY m.date ASC, m.ROWID ASC
"""


class IMessageAdapter:
    """Yield one raw record per iMessage from a local ``chat.db``.

    Args:
        db_path: the Messages SQLite file. Default: ``~/Library/Messages/chat.db``
            (only used as a fallback; tests pass an explicit fixture path). A
            missing file yields nothing — the sync lane skips gracefully.
        kind: the genesis ``kind`` stamped on every record (the source label).

    The adapter emits a ``chat_id`` field on every record so the sync entrypoint
    can apply the founder's allowlist BEFORE the body is ever screened or kept.
    """

    def __init__(
        self,
        db_path: str | os.PathLike[str] | None = None,
        *,
        kind: str = "imessage",
    ):
        self.db_path = (
            mcs_paths._norm(db_path) if db_path is not None else _DEFAULT_CHAT_DB
        )
        self.kind = kind

    def read(self) -> Iterator[dict[str, Any]]:
        if not self.db_path.is_file():
            return  # absent DB -> nothing (lane skips)
        conn = _connect_ro(self.db_path)
        if conn is None:
            return
        try:
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute(_QUERY)
            except sqlite3.DatabaseError:
                # Not a usable Messages DB (corrupt / unexpected schema) -> skip
                # the lane rather than crash the whole sync.
                return
            for row in cursor:
                yield self._record_from_row(row)
        finally:
            conn.close()

    def _record_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        chat_id = _str(row["chat_identifier"]) or _str(row["handle_id"]) or "unknown"
        handle = _str(row["handle_id"])
        is_from_me = bool(row["is_from_me"])
        # Stable per-message id: the guid (globally unique in chat.db); fall back
        # to the rowid so a guid-less row still gets a stable source_id.
        guid = _str(row["guid"]) or f"rowid-{row['rowid']}"
        participants = [p for p in (handle,) if p]
        return {
            "kind": self.kind,
            "source_type": self.kind,
            "source_id": guid,
            "chat_id": chat_id,
            "chat_name": _str(row["display_name"]) or chat_id,
            "locator": "",
            "text": _str(row["text"]),
            "title": _str(row["display_name"]) or chat_id,
            "observed_at": _apple_date_to_iso(row["date"]),
            "is_from_me": is_from_me,
            "participants": participants,
            "meta": {
                "adapter": "imessage",
                "is_from_me": is_from_me,
                "chat_id": chat_id,
            },
        }


def _connect_ro(path: Path) -> sqlite3.Connection | None:
    """Open the SQLite file strictly read-only (URI ``mode=ro``).

    Read-only is a hard guarantee here: the live Messages store must never be
    mutated by the brain. Returns None if the file can't be opened.
    """
    try:
        return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error:
        return None


def _str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _apple_date_to_iso(value: Any) -> str:
    """Convert a ``chat.db`` ``message.date`` to ISO-8601 UTC ``...Z``.

    Modern rows are nanoseconds since 2001-01-01; older rows are seconds. A
    null/zero/garbage value yields ``""`` so normalize falls back to now (the
    spine guarantees a sortable timestamp regardless).
    """
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return ""
    if raw <= 0:
        return ""
    seconds = raw / 1_000_000_000 if raw >= _NS_THRESHOLD else float(raw)
    dt = _APPLE_EPOCH + timedelta(seconds=seconds)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")
