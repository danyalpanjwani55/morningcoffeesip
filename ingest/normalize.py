"""Normalize — the SECOND station: coerce a raw source record into the canonical
shape a genesis ``Event`` needs.

De-welded from the company brain's ``normalize_event_record``. What carried over
(the reusable spirit): take a loose source dict and produce a canonical record
with a stable UTC timestamp, a source ref, participants, and the safe text. What
was deleted: the artifact-contract validation, the manifest/watermark run
record, the privacy coupling, and the company source taxonomy — none of which a
generic genesis Event needs.

The output is an ``Event`` (genesis_contracts) directly, so the pipeline can hand
a list straight to ``IngestedCorpus`` / ``run_genesis``. The pipeline reads
``event.meta`` for ``asserted_by`` / ``owner`` / ``provenance_tier`` and parses a
leading ``key = value`` line into a tracked fact, so adapters that know those
things put them in ``meta``; everything else is a non-fact context event (still
fully anchored). Stdlib-only; no network, no file I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

# The genesis Event lives one level up (repo root is on sys.path via the genesis
# shim, but the ingest package is imported as ``ingest`` from the repo root, so
# we reach genesis the same way the egress re-export does).
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GENESIS = os.path.join(_REPO_ROOT, "genesis")
for _p in (_REPO_ROOT, _GENESIS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from genesis_contracts import Event  # noqa: E402

# Field aliases an adapter might use for each canonical slot (first present
# wins). Generic — covers the common email/file/chat shapes without a company
# source table.
_TEXT_ALIASES = ("text", "body", "content", "message", "snippet")
_KIND_ALIASES = ("kind", "source_type", "source", "type")
_SOURCE_ID_ALIASES = ("source_id", "id", "message_id", "file_id", "path")
_LOCATOR_ALIASES = ("locator", "anchor", "line", "loc")
_TIME_ALIASES = ("observed_at", "occurred_at", "timestamp", "date", "sent_at", "created_at")
_PARTICIPANT_ALIASES = ("participants", "people", "from", "sender", "to", "recipients")

# meta keys the genesis pipeline reads off an Event.
_META_KEYS = ("asserted_by", "owner", "provenance_tier")


@dataclass(frozen=True)
class RawRecord:
    """A thin, explicit raw record an adapter can emit instead of a bare dict.

    Purely a convenience: ``normalize_record`` accepts either this or any
    mapping. ``meta`` carries the genesis-read keys (asserted_by / owner /
    provenance_tier) plus anything else an adapter wants to thread through.
    """

    kind: str
    text: str
    source_id: str
    locator: str = ""
    observed_at: str | None = None
    participants: Sequence[str] = field(default_factory=tuple)
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = {
            "kind": self.kind,
            "text": self.text,
            "source_id": self.source_id,
            "locator": self.locator,
            "participants": list(self.participants),
            **self.meta,
        }
        if self.observed_at is not None:
            d["observed_at"] = self.observed_at
        return d


def normalize_record(
    record: Mapping[str, Any] | RawRecord, *, ingested_at: str | None = None
) -> Event:
    """Coerce one raw record into a genesis ``Event``.

    - ``observed_at`` is normalized to an ISO-8601 UTC ``...Z`` string. A missing
      or unparseable timestamp falls back to ``ingested_at`` (or now), so an
      Event always sorts deterministically in ``events_since``.
    - ``participants`` is coerced to a tuple of plain string handles.
    - ``meta`` carries ``asserted_by`` / ``owner`` / ``provenance_tier`` when the
      record supplies them (the keys the genesis pipeline reads), plus the raw
      timestamp string under ``raw_observed_at`` for traceability.

    The ``event_id`` is the dedupe key when present (so the same source item maps
    to a stable Event id across runs); else it's derived from source_id+locator.
    """
    data = record.as_dict() if isinstance(record, RawRecord) else dict(record)

    kind = _first_str(data, _KIND_ALIASES) or "note"
    text = (_first_str(data, _TEXT_ALIASES) or "").strip()
    source_id = _first_str(data, _SOURCE_ID_ALIASES) or "unknown"
    locator = _first_str(data, _LOCATOR_ALIASES) or ""

    raw_time = _first_str(data, _TIME_ALIASES)
    observed_at = _to_iso_utc(raw_time, fallback=ingested_at)

    participants = _coerce_participants(
        _first_present(data, _PARTICIPANT_ALIASES)
    )

    meta: dict[str, Any] = {}
    for key in _META_KEYS:
        if data.get(key) not in (None, ""):
            meta[key] = data[key]
    if raw_time:
        meta["raw_observed_at"] = raw_time
    # carry an explicit event_id hint through if the adapter set one, else
    # derive a stable id from source_id (+ locator when present).
    event_id = _first_str(data, ("event_id", "dedupe_key"))
    if not event_id:
        event_id = f"{source_id}:{locator}" if locator else source_id

    return Event(
        event_id=event_id,
        observed_at=observed_at,
        kind=kind,
        text=text,
        source_id=source_id,
        locator=locator,
        participants=participants,
        meta=meta,
    )


def _first_present(data: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for k in keys:
        if k in data and data[k] not in (None, "", [], ()):
            return data[k]
    return None


def _first_str(data: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    value = _first_present(data, keys)
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value).strip() or None


def _coerce_participants(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items: Sequence[Any] = [value]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        handle = _participant_handle(item)
        if handle and handle not in seen:
            seen.add(handle)
            out.append(handle)
    return tuple(out)


def _participant_handle(item: Any) -> str:
    """Reduce a participant (string or dict) to one stable plain handle.

    Prefers an email, then a name/handle field, then the stringified value.
    Deliberately generic — no company alias table.
    """
    if isinstance(item, Mapping):
        for key in ("email", "address", "handle", "slug", "name", "display_name"):
            v = item.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip().lower() if key in ("email", "address") else v.strip()
        return ""
    text = str(item).strip()
    return text.lower() if "@" in text else text


def _to_iso_utc(raw: str | None, *, fallback: str | None) -> str:
    """Best-effort parse of a timestamp to ISO-8601 UTC ``...Z``.

    Accepts already-ISO strings (with ``Z`` or an offset) and naive ISO (assumed
    UTC). On failure, uses ``fallback`` (parsed the same way) or now-UTC. The
    point is a TOTAL function: an Event must always have a sortable timestamp."""
    parsed = _try_parse(raw)
    if parsed is None:
        parsed = _try_parse(fallback)
    if parsed is None:
        parsed = datetime.now(tz=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _try_parse(raw: str | None) -> datetime | None:
    if not raw or not str(raw).strip():
        return None
    text = str(raw).strip()
    # datetime.fromisoformat (3.9) doesn't accept a trailing 'Z'; swap it.
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        pass
    # RFC-2822 (email Date: headers) — parsed by the email adapter normally, but
    # tolerate it here too.
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(text)
        return dt if isinstance(dt, datetime) else None
    except (TypeError, ValueError, IndexError):
        return None
