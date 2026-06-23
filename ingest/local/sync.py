"""sync.py — the thin Mac sync agent's entrypoint (``python -m ingest.local.sync``).

This is the LOCAL half of the architecture split (master spec §6, full picture in
``docs/INGEST-ARCHITECTURE.md``). It runs ON THE FOUNDER'S MAC and does exactly
one thing: read the two on-device message stores that have no cloud API
(iMessage's ``chat.db`` + WhatsApp's ``ChatStorage.sqlite``), keep only messages
from people the founder **corresponds with** (the shared identity ``Allowlist``),
and write the resulting genesis **Events** to the **local brain store** as
proposals for the morning gate.

The allowlist (the rule the whole system turns on)
--------------------------------------------------
A message is ingested only if a participant is someone the founder *already
corresponds with* — derived from the email SENT folder (the email cloud routine
PRODUCES that correspondent set; the local lanes CONSUME it). ``sync.py`` builds
ONE ``ingest.allowlist.Allowlist`` from the correspondents source and applies it
to BOTH lanes:
  * **iMessage** yields RAW records; ``sync.py`` drops any whose handle isn't a
    correspondent (``Allowlist.contains`` on the record's ``chat_id``).
  * **WhatsApp** filters INTERNALLY (it resolves JIDs → identities first), so
    ``sync.py`` injects the same ``Allowlist`` into the adapter and consumes its
    already-admitted records.
The model is **opt-in, fail-closed**: an empty allowlist (no correspondents)
ingests nothing. (There is deliberately no "allow-all" — that would contradict
the data-boundary posture; the founder declares scope by who they correspond
with, not by a blanket switch.)

The rails, in code (not aspiration):
  * **Proposals-only / nothing sent.** Writes Events to a local JSONL store under
    the brain root. NEVER sends a message, calls a network, touches git, or moves
    money. The cloud routine + ``/morning`` decide what becomes brain truth.
  * **Allowlist-gated + sanitized.** Off-correspondent messages are dropped; every
    surviving body passes the egress/privacy gate via the spine — a secret /
    credential / PII-bearing message never becomes an Event.
  * **Data never leaves the Mac except as sanitized Events.** Raw bodies are read
    only as spine input; the only persisted output is the sanitized Event store
    the founder controls.
  * **Graceful absence.** A lane whose store/DB is absent (or whose adapter module
    won't import) is SKIPPED — a clean per-lane no-op, never a crash.
  * **``--dry-run``.** Does everything except write the store (counts only).

Usage::

    python -m ingest.local.sync                      # all lanes, default stores
    python -m ingest.local.sync --dry-run            # plan only, write nothing
    python -m ingest.local.sync --lane imessage
    python -m ingest.local.sync --imessage-db /path/chat.db \
                                --whatsapp-store /path/ChatStorage.sqlite
    python -m ingest.local.sync --correspondents /path/sent-correspondents.txt

Stdlib-only. The store is line-delimited JSON (JSONL): an append-only, idempotent
local record; re-running de-dups against what is already there (the Event id is
the spine's dedupe key).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import mcs_paths  # noqa: E402

from ingest.allowlist import Allowlist, build_allowlist, summarize  # noqa: E402
from ingest.local import LocalAdapterUnavailable, available_lanes, imessage_adapter, whatsapp_adapter  # noqa: E402
from ingest.pipeline import ingest_records  # noqa: E402

# Where the sync agent writes the sanitized Events on the local Mac. Under the
# brain root so it travels with the brain, not the repo. JSONL = one Event per
# line (append-only, idempotent re-read).
_STORE_REL = ("local", "events.jsonl")

# Default correspondents source: a newline/comma-separated list of the addresses
# the founder corresponds with (what the email cloud routine writes from the SENT
# folder). Resolved via mcs_paths so a clone has a documented place for it.
_CORRESPONDENTS_REL = ("sources", "sent-correspondents.txt")
ENV_CORRESPONDENTS = "MCS_CORRESPONDENTS"


@dataclass
class LaneResult:
    """What happened to one local lane this run."""

    lane: str
    read: int = 0                  # raw records the adapter yielded (post-internal-filter for WA)
    after_allowlist: int = 0       # records that passed the correspondent allowlist
    kept: int = 0                  # Events written (sanitized, deduped survivors)
    dropped_private: int = 0
    dropped_duplicate: int = 0
    dropped_empty: int = 0
    skipped_reason: str = ""       # why a lane was skipped (absent / unreadable)


@dataclass
class SyncResult:
    """The whole run: per-lane results + where (if anywhere) it wrote."""

    lanes: list[LaneResult] = field(default_factory=list)
    store_path: Path | None = None
    written: int = 0
    dry_run: bool = False
    allowlist_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def total_kept(self) -> int:
        return sum(l.kept for l in self.lanes)


def _build_imessage(path: Any) -> Any:
    try:
        return imessage_adapter(path)
    except LocalAdapterUnavailable:
        return None


def _build_whatsapp(path: Any, allowlist: Allowlist) -> Any:
    # WhatsApp filters internally, so the allowlist is injected at construction.
    try:
        return whatsapp_adapter(path, allowlist=allowlist)
    except LocalAdapterUnavailable:
        return None
    except TypeError:
        # An adapter build that doesn't accept allowlist= (older signature) —
        # construct bare; it will fail-closed internally. Defensive only.
        try:
            return whatsapp_adapter(path)
        except LocalAdapterUnavailable:
            return None


def run_sync(
    *,
    lanes: Iterable[str] | None = None,
    paths: dict[str, Any] | None = None,
    allowlist: Allowlist | None = None,
    store_path: str | os.PathLike[str] | None = None,
    dry_run: bool = False,
    ingested_at: str | None = None,
) -> SyncResult:
    """Run the local sync over the requested lanes and return a ``SyncResult``.

    Per lane: get admitted raw records (iMessage: read all, then drop non-
    correspondent handles; WhatsApp: the adapter already filtered by the injected
    allowlist) → run survivors through ``ingest_records`` (sanitize → dedup →
    normalize) → accumulate Events. After all lanes, write the Events to the
    local store (unless ``dry_run``), de-duped against what is already there by
    Event id.

    A lane whose adapter is unavailable or whose store is absent contributes a
    ``present=False`` ``LaneResult`` and writes nothing — a clean per-lane no-op.
    An empty ``allowlist`` (no correspondents) admits nothing — fail-closed.
    """
    selected = tuple(lanes) if lanes is not None else available_lanes()
    paths = paths or {}
    allowlist = allowlist if allowlist is not None else build_allowlist([], contacts={})
    ingested_at = ingested_at or _now_iso()

    result = SyncResult(dry_run=dry_run, allowlist_summary=summarize(allowlist))
    all_events: list[Any] = []

    for lane in selected:
        lane_res = LaneResult(lane=lane)
        adapter = (
            _build_imessage(paths.get(lane))
            if lane == "imessage"
            else _build_whatsapp(paths.get(lane), allowlist)
            if lane == "whatsapp"
            else None
        )
        if adapter is None:
            lane_res.skipped_reason = "adapter_unavailable"
            result.lanes.append(lane_res)
            continue

        try:
            raw = list(adapter.read())
        except Exception:  # noqa: BLE001 — a broken store skips the lane, not the run
            lane_res.skipped_reason = "store_unreadable"
            result.lanes.append(lane_res)
            continue

        # An absent store and an empty store both yield zero rows here — both are
        # correctly a no-op. We report read/kept (what a founder cares about)
        # rather than guess which it was; the adapter handles absence internally.
        lane_res.read = len(raw)

        if lane == "imessage":
            # iMessage yields RAW records; apply the correspondent allowlist here
            # (its chat_id is the counterparty handle — a phone/email/identity).
            scoped = [r for r in raw if allowlist.contains(r.get("chat_id"))]
        else:
            # WhatsApp already filtered internally by the injected allowlist.
            scoped = raw
        lane_res.after_allowlist = len(scoped)

        ingest = ingest_records(scoped, ingested_at=ingested_at)
        lane_res.kept = ingest.kept
        lane_res.dropped_private = ingest.dropped_private
        lane_res.dropped_duplicate = ingest.dropped_duplicate
        lane_res.dropped_empty = ingest.dropped_empty
        all_events.extend(ingest.corpus.all_events())
        result.lanes.append(lane_res)

    store = (
        mcs_paths._norm(store_path)
        if store_path is not None
        else mcs_paths.brain_root().joinpath(*_STORE_REL)
    )
    result.store_path = store
    if not dry_run:
        result.written = _append_events(store, all_events)

    return result


def iter_local_records(
    allowlist: Allowlist,
    *,
    paths: dict[str, Any] | None = None,
    lanes: Iterable[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield the allowlist-scoped RAW records from the local message lanes.

    The same admission rule ``run_sync`` applies, but yielding the raw records
    (not writing a store) — so another front door (``run.py``'s genesis on-ramp)
    can fold the on-device messages into its corpus through the SAME shared spine
    it already runs over email + notes. One place owns the per-lane scope rule:

      * **iMessage** is a dumb reader → drop any record whose ``chat_id`` (the
        counterparty handle) is not a correspondent.
      * **WhatsApp** filters INTERNALLY against the injected allowlist → its
        records are already admitted; pass them straight through.

    GRACEFUL ABSENCE is total: a lane whose adapter module is missing, or whose
    store file is absent / unreadable (e.g. not macOS, or no Full Disk Access),
    contributes NOTHING — never raises. So a clone on Linux, or a Mac before the
    grant, simply gets zero local records and the on-ramp runs on email+notes
    alone. An empty ``allowlist`` admits nobody (fail-closed).
    """
    selected = tuple(lanes) if lanes is not None else available_lanes()
    paths = paths or {}
    for lane in selected:
        adapter = (
            _build_imessage(paths.get(lane))
            if lane == "imessage"
            else _build_whatsapp(paths.get(lane), allowlist)
            if lane == "whatsapp"
            else None
        )
        if adapter is None:
            continue  # adapter module unavailable -> skip the lane
        try:
            raw = list(adapter.read())
        except Exception:  # noqa: BLE001 — a broken/absent store skips the lane
            continue
        if lane == "imessage":
            for record in raw:
                if allowlist.contains(record.get("chat_id")):
                    yield record
        else:
            yield from raw  # WhatsApp already filtered internally


def _append_events(store: Path, events: list[Any]) -> int:
    """Append new Events to the JSONL store, de-duped by Event id.

    Reads the existing store's Event ids first so a re-run never writes a row it
    already has (idempotent). Creates the store's parent dir if needed. Returns
    the number of NEW rows written. Never writes raw private content — Events are
    already sanitized by the spine before they reach here.
    """
    if not events:
        # Nothing to write — do NOT create an empty file (keep a no-op truly inert).
        return 0
    seen = _existing_ids(store)
    new_rows = []
    for ev in events:
        if ev.event_id in seen:
            continue
        seen.add(ev.event_id)
        new_rows.append(_event_to_json(ev))
    if not new_rows:
        return 0
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("a", encoding="utf-8") as fh:
        for row in new_rows:
            fh.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    return len(new_rows)


def _existing_ids(store: Path) -> set[str]:
    if not store.is_file():
        return set()
    ids: set[str] = set()
    try:
        with store.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ids.add(json.loads(line).get("event_id", ""))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return set()
    ids.discard("")
    return ids


def _event_to_json(ev: Any) -> dict[str, Any]:
    """Serialize a genesis Event to a plain JSON-safe dict for the store."""
    return {
        "event_id": ev.event_id,
        "observed_at": ev.observed_at,
        "kind": ev.kind,
        "text": ev.text,
        "source_id": ev.source_id,
        "locator": ev.locator,
        "participants": list(ev.participants),
        "meta": dict(ev.meta),
    }


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Correspondents source                                                       #
# --------------------------------------------------------------------------- #


def _correspondents_path(explicit: str | os.PathLike[str] | None) -> Path:
    if explicit is not None:
        return mcs_paths._norm(explicit)
    if os.environ.get(ENV_CORRESPONDENTS):
        return mcs_paths._norm(os.environ[ENV_CORRESPONDENTS])
    return mcs_paths.brain_root().joinpath(*_CORRESPONDENTS_REL)


def _load_correspondents(explicit: str | os.PathLike[str] | None) -> list[str]:
    """Read the correspondents list (newline/comma-separated identities).

    A missing file yields an empty list — the lanes then admit nothing
    (fail-closed). Lines starting with ``#`` are comments.
    """
    path = _correspondents_path(explicit)
    if not path.is_file():
        return []
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.extend(part.strip() for part in line.split(",") if part.strip())
    return out


def build_allowlist_from_email(
    email_adapter: Any,
    *,
    contacts: Mapping[str, Iterable[str]] | None = None,
) -> Allowlist:
    """Build the shared ``Allowlist`` straight from an email lane (the producer).

    This is the producer→consumer seam in ONE call: the email lane is the thing
    that DEFINES who the founder corresponds with (it harvests the SENT folder's
    To/Cc into ``sent_correspondents``); the message lanes CONSUME the resulting
    ``Allowlist``. Here we drain the email adapter (which populates
    ``sent_correspondents`` as a side effect of reading) and hand that exact set
    to :func:`ingest.allowlist.build_allowlist`.

    ``email_adapter`` is any object exposing the ``EmailAdapter`` contract — a
    ``build()`` (or ``read()``) that, once drained, leaves the harvested
    correspondents on ``.sent_correspondents``. We call ``build()`` when present
    (it drains eagerly) else exhaust ``read()``; either way the correspondent set
    is populated before we read it.

    ``contacts`` is threaded through to ``build_allowlist`` so a correspondent
    emailed at one address is also recognized when they text from a phone/handle
    (``None`` resolves macOS Contacts on a Mac; pass ``{}`` to force email-only).

    An email lane that found no Sent correspondents yields an EMPTY allowlist —
    which the message lanes treat as fail-closed (admit nobody). That is the
    correct, safe default, not an error.
    """
    builder = getattr(email_adapter, "build", None)
    if callable(builder):
        builder()
    else:
        # No eager build(): exhaust read() so the harvest side effect runs.
        for _ in email_adapter.read():
            pass
    correspondents = getattr(email_adapter, "sent_correspondents", None) or set()
    return build_allowlist(sorted(correspondents), contacts=contacts)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _format_report(result: SyncResult) -> str:
    lines = ["MorningCoffeeSip — local Mac sync agent"]
    lines.append(f"  mode: {'DRY-RUN (wrote nothing)' if result.dry_run else 'write'}")
    al = result.allowlist_summary
    lines.append(
        f"  allowlist: {al.get('token_total', 0)} correspondent token(s)"
        + ("  — EMPTY (no correspondents; nothing will ingest)" if not al.get("token_total") else "")
    )
    for l in result.lanes:
        if l.skipped_reason == "adapter_unavailable":
            lines.append(f"  [{l.lane}] skipped — adapter module unavailable")
        elif l.skipped_reason == "store_unreadable":
            lines.append(f"  [{l.lane}] skipped — store present but unreadable")
        elif l.read == 0:
            lines.append(f"  [{l.lane}] nothing read (store absent or empty)")
        else:
            lines.append(
                f"  [{l.lane}] read {l.read} -> admitted {l.after_allowlist} -> kept {l.kept} "
                f"(private {l.dropped_private}, dup {l.dropped_duplicate}, empty {l.dropped_empty})"
            )
    if result.store_path is not None:
        verb = "would write" if result.dry_run else "wrote"
        lines.append(f"  store: {result.store_path}")
        lines.append(
            f"  {verb} {result.total_kept if result.dry_run else result.written} new event(s)"
        )
    lines.append("  rails: proposals-only · nothing sent · no git · no network")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ingest.local.sync",
        description="The local Mac sync agent: ingest correspondent-allowlisted "
        "iMessage + WhatsApp into the local brain store as sanitized, "
        "proposals-only Events.",
    )
    parser.add_argument(
        "--lane",
        action="append",
        choices=list(available_lanes()),
        help="Run only this lane (repeatable). Default: all lanes.",
    )
    parser.add_argument("--imessage-db", help="Path to the Messages chat.db.")
    parser.add_argument("--whatsapp-store", help="Path to the WhatsApp ChatStorage.sqlite.")
    parser.add_argument(
        "--correspondents",
        help="Path to the correspondents list (else $MCS_CORRESPONDENTS / brain default). "
        "On a Mac, contact phones/handles auto-resolve from Contacts.",
    )
    parser.add_argument("--store", help="Path to the local Event store (JSONL).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do everything except write the store (counts only).",
    )
    args = parser.parse_args(argv)

    paths: dict[str, Any] = {}
    if args.imessage_db:
        paths["imessage"] = args.imessage_db
    if args.whatsapp_store:
        paths["whatsapp"] = args.whatsapp_store

    correspondents = _load_correspondents(args.correspondents)
    # contacts=None lets build_allowlist resolve phones/handles from macOS
    # Contacts for the listed correspondents (fail-open to email-only off-Mac).
    allowlist = build_allowlist(correspondents)

    result = run_sync(
        lanes=args.lane,
        paths=paths,
        allowlist=allowlist,
        store_path=args.store,
        dry_run=args.dry_run,
    )
    print(_format_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
