"""schema.py — the NORMALIZED connector-record contract.

This is the seam between the two halves of the cloud routine: the Claude Gmail /
Drive / Calendar **connectors** pull the data (runtime, lane B); they map each
item INTO one of the shapes here; and the processing (``ingest.cloud.process``)
reads ONLY these shapes. So the connector pull and the processing are decoupled —
the processing is testable against record fixtures without any live account.

The three records mirror exactly the three connectors the live company routine
reads (Gmail / Drive / Calendar), each carrying the fields the processing needs:

  * ``GmailMessage`` — ``message_id`` · ``sender`` · ``to`` · ``cc`` · ``date`` ·
    ``body`` · ``is_sent``. ``is_sent`` is the connector's own answer to "did the
    founder send this?" (the routine knows, from the Sent label / the ``from`` ==
    the account, exactly as the live ``email-classifier``/``email_source`` do).
    The processing trusts that flag — it does NOT re-derive sent-ness — and uses
    ``to``+``cc`` of the sent messages to seed the correspondent allowlist.
  * ``DriveDoc`` — ``id`` · ``title`` · ``content`` · ``modified``.
  * ``CalendarEvent`` — ``id`` · ``title`` · ``attendees`` · ``start`` · ``end``.

Each record exposes ``to_raw_record()`` → a plain dict in the shape the EXISTING
ingest spine already consumes (``ingest.pipeline.ingest_records`` →
normalize/sanitize/dedupe), so the cloud lane reuses the whole spine unchanged
rather than re-implementing event construction. The Gmail mapping is the one with
real logic (it folds subject + body into the event text and lists From/To/Cc as
participants); Drive/Calendar are thin.

Stdlib-only; no network, no file I/O (the entrypoint does the file read).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

# The genesis ``kind`` stamped on each record's event. Kept aligned with the
# local lanes' vocabulary ("email" / "drive" / "calendar") so a downstream
# pillar router treats a cloud-Gmail event the same as a local email event.
KIND_GMAIL = "email"
KIND_DRIVE = "drive"
KIND_CALENDAR = "calendar"


def _clean(value: Any) -> str:
    """A trimmed string (``""`` for None / non-str)."""
    return value.strip() if isinstance(value, str) else ""


def _addr_list(value: Any) -> tuple[str, ...]:
    """Coerce a To/Cc/attendees value to a tuple of lowercased address strings.

    Accepts a list/tuple of strings, or a single comma-or-semicolon-separated
    string (some connectors hand back ``"a@x.com, b@y.com"``). Lowercased so the
    correspondent set matches case-insensitively; de-duped, order-preserving.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        parts: Sequence[Any] = [value] if ("," not in value and ";" not in value) else _split_addrs(value)
    elif isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        parts = [value]
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        addr = _clean(part).lower()
        if addr and addr not in seen:
            seen.add(addr)
            out.append(addr)
    return tuple(out)


def _split_addrs(value: str) -> list[str]:
    out: list[str] = []
    for chunk in value.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            out.append(chunk)
    return out


@dataclass(frozen=True)
class GmailMessage:
    """One Gmail message as the connector hands it over (post-pull).

    ``is_sent`` is the connector's authoritative sent-vs-inbound answer — the
    processing trusts it rather than re-deriving (the routine, like the live
    ``email_source``, already knows from the Sent label / the account ``from``).
    ``to`` / ``cc`` of the SENT messages seed the correspondent allowlist; an
    inbound message is kept only if its ``sender`` is in that set.
    """

    message_id: str
    sender: str = ""
    to: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    date: str = ""
    body: str = ""
    is_sent: bool = False
    subject: str = ""

    @classmethod
    def from_obj(cls, obj: Mapping[str, Any]) -> "GmailMessage":
        return cls(
            message_id=_clean(obj.get("message_id") or obj.get("id")),
            sender=_clean(obj.get("sender") or obj.get("from")).lower(),
            to=_addr_list(obj.get("to")),
            cc=_addr_list(obj.get("cc")),
            date=_clean(obj.get("date") or obj.get("observed_at")),
            body=_clean(obj.get("body") or obj.get("text") or obj.get("snippet")),
            is_sent=bool(obj.get("is_sent")),
            subject=_clean(obj.get("subject") or obj.get("title")),
        )

    def participants(self) -> tuple[str, ...]:
        out: list[str] = []
        seen: set[str] = set()
        for addr in (self.sender, *self.to, *self.cc):
            a = addr.strip().lower()
            if a and a not in seen:
                seen.add(a)
                out.append(a)
        return tuple(out)

    def to_raw_record(self) -> dict[str, Any]:
        """Map to the spine's raw-record dict shape (sanitize→normalize→dedup).

        The event text is the subject + body (subject first, so an inbound mail
        whose body the routine truncated still anchors on its subject). The
        stable id is the Gmail ``message_id`` (the spine keys dedupe off it)."""
        text = "\n".join(t for t in (self.subject, self.body) if t)
        return {
            "kind": KIND_GMAIL,
            "source_type": KIND_GMAIL,
            "source_id": self.message_id or "gmail-message",
            "locator": "",
            "text": text,
            "subject": self.subject,
            "title": self.subject,
            "observed_at": self.date,
            "participants": list(self.participants()),
            "meta": {"adapter": "cloud_gmail", "from": [self.sender] if self.sender else []},
        }


@dataclass(frozen=True)
class DriveDoc:
    """One Drive document as the connector hands it over (post-pull)."""

    id: str
    title: str = ""
    content: str = ""
    modified: str = ""

    @classmethod
    def from_obj(cls, obj: Mapping[str, Any]) -> "DriveDoc":
        return cls(
            id=_clean(obj.get("id") or obj.get("file_id")),
            title=_clean(obj.get("title") or obj.get("name")),
            content=_clean(obj.get("content") or obj.get("text") or obj.get("body")),
            modified=_clean(obj.get("modified") or obj.get("modified_at") or obj.get("observed_at")),
        )

    def to_raw_record(self) -> dict[str, Any]:
        text = "\n".join(t for t in (self.title, self.content) if t)
        return {
            "kind": KIND_DRIVE,
            "source_type": KIND_DRIVE,
            "source_id": self.id or "drive-doc",
            "locator": "",
            "text": text,
            "title": self.title,
            "observed_at": self.modified,
            "participants": [],
            "meta": {"adapter": "cloud_drive"},
        }


@dataclass(frozen=True)
class CalendarEvent:
    """One Calendar event as the connector hands it over (post-pull)."""

    id: str
    title: str = ""
    attendees: tuple[str, ...] = ()
    start: str = ""
    end: str = ""

    @classmethod
    def from_obj(cls, obj: Mapping[str, Any]) -> "CalendarEvent":
        return cls(
            id=_clean(obj.get("id") or obj.get("event_id")),
            title=_clean(obj.get("title") or obj.get("summary")),
            attendees=_addr_list(obj.get("attendees")),
            start=_clean(obj.get("start") or obj.get("observed_at")),
            end=_clean(obj.get("end")),
        )

    def to_raw_record(self) -> dict[str, Any]:
        when = self.start + (f" – {self.end}" if self.end else "")
        text = f"{self.title} ({when})" if when else self.title
        return {
            "kind": KIND_CALENDAR,
            "source_type": KIND_CALENDAR,
            "source_id": self.id or "calendar-event",
            "locator": "",
            "text": text,
            "title": self.title,
            "observed_at": self.start,
            "participants": list(self.attendees),
            "meta": {"adapter": "cloud_calendar"},
        }


@dataclass
class NormalizedRecords:
    """A parsed connector dump: the three lanes' records, ready for processing.

    This is precisely what a connector run produces and what
    ``process_cloud_records`` consumes. Built from a JSON object shaped
    ``{"gmail": [...], "drive": [...], "calendar": [...]}`` (any lane optional —
    a missing/absent connector is simply an empty list, never an error).
    """

    gmail: list[GmailMessage] = field(default_factory=list)
    drive: list[DriveDoc] = field(default_factory=list)
    calendar: list[CalendarEvent] = field(default_factory=list)

    def is_empty(self) -> bool:
        """True iff every lane is empty (the idle-run signal — nothing new)."""
        return not (self.gmail or self.drive or self.calendar)


def normalized_records_from_obj(obj: Mapping[str, Any]) -> NormalizedRecords:
    """Build :class:`NormalizedRecords` from an already-parsed JSON-like object.

    Lane keys are tolerant of singular/plural and a couple of aliases so a
    connector dump that names them slightly differently still parses. Any lane
    absent → empty (a connector that wasn't attached is not an error).
    """
    if not isinstance(obj, Mapping):
        raise ValueError("cloud records must be a JSON object with lane keys")
    gmail_raw = _lane(obj, ("gmail", "email", "messages"))
    drive_raw = _lane(obj, ("drive", "docs", "files"))
    cal_raw = _lane(obj, ("calendar", "events", "cal"))
    return NormalizedRecords(
        gmail=[GmailMessage.from_obj(m) for m in gmail_raw],
        drive=[DriveDoc.from_obj(d) for d in drive_raw],
        calendar=[CalendarEvent.from_obj(e) for e in cal_raw],
    )


def normalized_records_from_json(text: str) -> NormalizedRecords:
    """Parse a connector-dump JSON string into :class:`NormalizedRecords`."""
    return normalized_records_from_obj(json.loads(text))


def _lane(obj: Mapping[str, Any], keys: Iterable[str]) -> list[Mapping[str, Any]]:
    for key in keys:
        if key in obj and obj[key] is not None:
            value = obj[key]
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"cloud lane {key!r} must be a list of records")
            return [v for v in value if isinstance(v, Mapping)]
    return []
