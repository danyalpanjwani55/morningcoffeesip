"""refresh.py — the cloud lane entrypoint (``python -m ingest.cloud.refresh``).

The PROCESSING run, end to end, over a connector dump the routine already pulled:

    read normalized records (a JSON file / stdin)
      → process_cloud_records (Gmail SENT-filter → allowlist → spine)
      → run_genesis (claims → resolve → pillars → meta-initiatives → roster)
      → write PROPOSALS (operator-gated; NOTHING applied, NOTHING sent, no git)

This is the cloud analogue of the local ``python -m ingest.local.sync``, and the
code half of the routine ``docs/CLOUD-ROUTINE.md`` specifies. It does **not**
authenticate or pull — the Claude Gmail/Drive/Calendar connectors do that inside
the scheduled routine (runtime); this entrypoint consumes their output as the
normalized records (``ingest.cloud.schema``). So the input is a JSON dump shaped::

    {"gmail": [ {message_id, sender, to, cc, date, body, is_sent}, ... ],
     "drive": [ {id, title, content, modified}, ... ],
     "calendar": [ {id, title, attendees, start, end}, ... ]}

Usage::

    python -m ingest.cloud.refresh --records pulled.json
    cat pulled.json | python -m ingest.cloud.refresh         # stdin
    python -m ingest.cloud.refresh --records pulled.json --user-email me@co.com
    python -m ingest.cloud.refresh --records pulled.json --dry-run   # write nothing
    python -m ingest.cloud.refresh --records pulled.json --out /path/proposals.json

THE RAILS (CODE, not aspiration — the same ones every other entry point enforces):
  * **Proposals-only.** ``run_genesis`` emits ``status="proposed"`` only; this
    writes them to a proposals file for ``/morning`` and applies NOTHING.
  * **Privacy gate.** The spine drops any record carrying a secret/credential/PII
    before it becomes an event (count surfaced; content never reaches the model).
  * **Data-boundary.** Every model prompt is ``egress.guard``-ed inside genesis.
  * **No OAuth / no network / no git / no sends / no money.** This reads a local
    JSON dump and writes a local proposals file; nothing else.
  * **Idle = inert.** An empty dump writes nothing and exits cleanly (no-op).

Built-in OFFLINE model: like ``run.py``, this routes ALL model judgment through
an injected ``llm`` and ships ``run.py``'s deterministic ``OfflineGenesisLLM`` so
it runs with NO API key. The engine's safety floors (anchor-or-drop, the roster
MIN_EVIDENCE gate) still decide what survives. Stdlib-only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_GENESIS = os.path.join(_REPO_ROOT, "genesis")
for _p in (_REPO_ROOT, _GENESIS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import mcs_paths  # noqa: E402

from genesis_contracts import EgressGate, ReviewPacket  # noqa: E402
from genesis_pipeline import run_genesis  # noqa: E402

from ingest.cloud.process import CloudProcessResult, process_cloud_records  # noqa: E402
from ingest.cloud.schema import NormalizedRecords, normalized_records_from_json  # noqa: E402

# Reuse run.py's offline model + base roster so the cloud lane behaves exactly
# like the on-ramp's genesis pass (one modeling layer, one roster template) —
# not a second, drifting copy.
from run import BASE_ROSTER, OfflineGenesisLLM  # noqa: E402

# Where the cloud lane writes its proposals on a real run. Under the brain root
# (travels with the brain, not the repo), beside the local lane's event store.
# JSON (not JSONL): one packet = pillars summary + the proposed items.
_PROPOSALS_REL = ("cloud", "proposals.json")


def run_refresh(
    records: NormalizedRecords,
    *,
    user_email: str = "",
    contacts: dict[str, Any] | None = None,
    llm: Any = None,
    egress: EgressGate | None = None,
    out_path: str | os.PathLike[str] | None = None,
    dry_run: bool = False,
    today: str | None = None,
    write_drafts: bool = False,
) -> dict[str, Any]:
    """Run the cloud processing + genesis pass and (unless dry-run) write proposals.

    Returns a structured summary (the same shape tests assert on): the
    ``CloudProcessResult``, the ``ReviewPacket`` (or ``None`` when idle), the
    resolved proposals-file path, and whether anything was written.

    ``write_drafts`` is OFF by default: a cloud refresh emits PROPOSALS, and
    writing genesis pillar drafts mutates ``genesis/out`` as a side effect, which
    a proposals-only run should not do implicitly. (The on-ramp ``run.py`` writes
    drafts deliberately; this lane keeps its only durable write the proposals
    file under the brain root.)
    """
    egress = egress or EgressGate()
    llm = llm or OfflineGenesisLLM()
    today = today or time.strftime("%Y-%m-%d", time.gmtime())

    processed: CloudProcessResult = process_cloud_records(
        records, user_email=user_email, contacts=contacts, gate=egress
    )

    out_path = (
        mcs_paths._norm(out_path)
        if out_path is not None
        else mcs_paths.brain_root().joinpath(*_PROPOSALS_REL)
    )

    # Idle / nothing-usable → write nothing, return a clean no-op. An empty dump
    # (no connector delta) OR a dump whose every record was dropped (private /
    # empty / off-correspondent) both correctly produce no proposals.
    if records.is_empty() or processed.kept == 0:
        return {
            "processed": processed,
            "packet": None,
            "out_path": out_path,
            "written": False,
            "reason": "idle_no_records" if records.is_empty() else "no_events_after_filter",
        }

    packet: ReviewPacket = run_genesis(
        processed.corpus,
        roster=list(BASE_ROSTER),
        since="inception",
        llm=llm,
        egress=egress,
        write_drafts=write_drafts,
        today=today,
    )

    written = False
    if not dry_run:
        _write_proposals(out_path, packet, processed, today=today)
        written = True

    return {
        "processed": processed,
        "packet": packet,
        "out_path": out_path,
        "written": written,
        "reason": "" if written else ("dry_run" if dry_run else "no_write"),
    }


def _write_proposals(
    out_path: Any, packet: ReviewPacket, processed: CloudProcessResult, *, today: str
) -> None:
    """Write the operator-gated proposals packet to ``out_path`` (JSON).

    Every proposal is ``status="proposed"`` (asserted by ``run_genesis``); this
    serializes them + the Type-2 "In plain terms" operator summary + a non-
    sensitive run accounting. NO raw private content is written — the summary is
    the review surface's already-sanitized prose, and proposals carry rationale +
    anchor refs (source ids/locators), never message bodies.
    """
    out_path = mcs_paths._norm(out_path)
    payload = {
        "generated": today,
        "lane": "cloud",
        "status": "proposed",          # the whole file is a gate surface
        "applied": False,
        "summary_md": packet.summary_md,
        "run": {
            "gmail_sent": processed.gmail_sent,
            "gmail_inbound": processed.gmail_inbound,
            "gmail_admitted": processed.gmail_admitted,
            "drive_seen": processed.drive_seen,
            "calendar_seen": processed.calendar_seen,
            "kept": processed.kept,
            "dropped_private": processed.dropped_private,
            "dropped_duplicate": processed.dropped_duplicate,
            "dropped_empty": processed.dropped_empty,
            "allowlist": processed.allowlist_summary,
        },
        "pillars": {
            name: {"summary": st.summary, "anchor_count": st.anchor_count}
            for name, st in packet.pillars.items()
        },
        "proposals": [_proposal_to_json(p) for p in packet.proposals],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _proposal_to_json(p: Any) -> dict[str, Any]:
    return {
        "id": p.id,
        "type": p.type,
        "status": p.status,
        "confidence": p.confidence,
        "rationale": p.rationale,
        "payload": dict(p.payload),
        "source_anchors": [
            {"source_id": a.source_id, "kind": a.kind, "locator": a.locator}
            for a in p.source_anchors
        ],
    }


def _read_records_text(records_arg: str | None) -> str:
    """The connector-dump JSON text: from ``--records <file>`` or stdin."""
    if records_arg:
        path = mcs_paths._norm(records_arg)
        if not path.is_file():
            raise SystemExit(f"--records file not found: {path}")
        return path.read_text(encoding="utf-8")
    data = sys.stdin.read()
    if not data.strip():
        raise SystemExit(
            "No records: pass --records <file.json> or pipe a connector dump on stdin."
        )
    return data


def _format_report(summary: dict[str, Any]) -> str:
    processed: CloudProcessResult = summary["processed"]
    lines = ["MorningCoffeeSip — cloud ingest refresh (processing)"]
    al = processed.allowlist_summary
    lines.append(
        f"  allowlist : {al.get('token_total', 0)} correspondent token(s) "
        f"from {processed.gmail_sent} sent Gmail message(s)"
        + ("  — EMPTY (no correspondents; inbound mail admits nobody)" if not al.get("token_total") else "")
    )
    lines.append(
        f"  gmail     : {processed.gmail_inbound} inbound seen → "
        f"{processed.gmail_admitted} admitted (correspondents only)"
    )
    lines.append(f"  drive     : {processed.drive_seen} doc(s)")
    lines.append(f"  calendar  : {processed.calendar_seen} event(s)")
    lines.append(
        f"  kept      : {processed.kept} event(s) → corpus "
        f"(private {processed.dropped_private}, dup {processed.dropped_duplicate}, "
        f"empty {processed.dropped_empty})"
    )
    packet = summary.get("packet")
    if packet is not None:
        lines.append(
            f"  proposals : {len(packet.proposals)} "
            f"(all status=proposed — nothing applied)"
        )
        verb = "would write" if not summary["written"] else "wrote"
        lines.append(f"  out       : {summary['out_path']}  ({verb})")
    else:
        lines.append(f"  proposals : 0 — {summary.get('reason', 'idle')} (clean no-op)")
    lines.append("  rails: proposals-only · no OAuth · no network · no git · nothing sent")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ingest.cloud.refresh",
        description=(
            "The cloud-ingest PROCESSING run: read a connector dump (Gmail/Drive/"
            "Calendar records the Claude connectors already pulled), apply the "
            "Gmail SENT-folder correspondent filter, run it through the shared "
            "ingest spine + genesis, and write operator-gated PROPOSALS. No OAuth, "
            "no network, no git, nothing sent."
        ),
    )
    parser.add_argument(
        "--records",
        help="Path to the connector-dump JSON (else read it from stdin).",
    )
    parser.add_argument(
        "--user-email",
        default="",
        help="The founder's own address (excluded from the correspondent set).",
    )
    parser.add_argument("--out", help="Path to write the proposals JSON (else the brain default).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do everything except write the proposals file (counts only).",
    )
    args = parser.parse_args(argv)

    records = normalized_records_from_json(_read_records_text(args.records))
    summary = run_refresh(
        records,
        user_email=args.user_email,
        out_path=args.out,
        dry_run=args.dry_run,
    )
    print(_format_report(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
