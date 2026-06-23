"""process.py — the cloud PROCESSING: normalized records → genesis corpus.

The middle of the cloud lane. It consumes :class:`ingest.cloud.schema`
records (already pulled by the connectors) and runs them through the SAME spine
the rest of the system uses, applying the one rule the whole system turns on to
the Gmail lane. No network, no OAuth, no writes — pure processing.

The pipeline, in order:

  1. **Gmail SENT-folder filter (the de-spam rule).** Split the Gmail records on
     ``is_sent``. From the SENT messages, harvest the founder's correspondents —
     reusing the CANONICAL ``harvest_sent_correspondents`` (the exact To+Cc rule
     ``EmailAdapter`` uses), NOT a fork. Feed that exact set to the CANONICAL
     ``ingest.allowlist.build_allowlist`` (the ONE allowlist; the cloud lane
     FEEDS it). Then keep an inbound Gmail message ONLY if its ``sender`` is a
     correspondent — newsletters / cold outreach / spam (senders the founder
     never wrote to) are dropped. The founder's own SENT mail seeds the
     allowlist but is not itself re-ingested as inbound (same as the mbox lane).
  2. **Drive + Calendar** → kept as-is (no allowlist gate — they're the
     founder's own shared docs + their own calendar; there is no spam analogue).
  3. **Spine** → every surviving record (inbound Gmail + Drive + Calendar) goes
     through ``ingest_records`` (sanitize via ``mcs_egress`` → dedup →
     normalize). A secret / credential / PII-bearing item is dropped there, like
     every other lane; the founder's clean substance flows into an
     ``IngestedCorpus`` ``run_genesis`` consumes directly.

The result (``CloudProcessResult``) carries the corpus alongside a full, non-
sensitive accounting (counts + reason codes only — never raw content), shaped
like the local ``sync.py`` ``SyncResult`` so the two read consistently.

Stdlib-only.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from mcs_egress import EgressGate  # noqa: E402

from ingest.adapters.email_source import harvest_sent_correspondents  # noqa: E402
from ingest.allowlist import Allowlist, build_allowlist, summarize  # noqa: E402
from ingest.cloud.schema import GmailMessage, NormalizedRecords  # noqa: E402
from ingest.corpus import IngestedCorpus  # noqa: E402
from ingest.pipeline import ingest_records  # noqa: E402


@dataclass
class CloudProcessResult:
    """The corpus the cloud lane produced + a full accounting of the run.

    Counts only — no field here ever carries a raw private body (the spine
    sanitizes before an Event exists; this struct exposes dispositions, not
    content). ``allowlist_summary`` is token COUNTS by kind, never the tokens.
    """

    corpus: IngestedCorpus
    allowlist: Allowlist
    gmail_sent: int = 0              # SENT Gmail messages (seed the allowlist)
    gmail_inbound: int = 0          # inbound Gmail messages seen
    gmail_admitted: int = 0         # inbound kept by the correspondent allowlist
    drive_seen: int = 0
    calendar_seen: int = 0
    kept: int = 0                   # Events in the corpus (spine survivors)
    dropped_private: int = 0
    dropped_duplicate: int = 0
    dropped_empty: int = 0
    allowlist_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def is_idle(self) -> bool:
        """True iff nothing at all was seen — the clean-no-op signal."""
        return (self.gmail_sent + self.gmail_inbound + self.drive_seen + self.calendar_seen) == 0


def correspondents_from_gmail(gmail: list[GmailMessage], *, user_email: str = "") -> set[str]:
    """Harvest the founder's correspondents from the SENT Gmail messages.

    The cloud-lane analogue of ``EmailAdapter._collect_correspondents``, sharing
    its exact rule via ``harvest_sent_correspondents``: the To + Cc of every
    ``is_sent`` message, with the founder's own address excluded. This is the
    seed for the ONE ``build_allowlist`` — the cloud lane feeds the canonical
    allowlist, it does not own a second one.
    """
    sent_recipient_lists = (
        [*m.to, *m.cc] for m in gmail if m.is_sent
    )
    return harvest_sent_correspondents(sent_recipient_lists, user_email=user_email)


def process_cloud_records(
    records: NormalizedRecords,
    *,
    user_email: str = "",
    contacts: dict[str, Any] | None = None,
    gate: EgressGate | None = None,
    ingested_at: str | None = None,
    allowlist: Allowlist | None = None,
) -> CloudProcessResult:
    """Process pulled connector records into a genesis corpus (the cloud lane).

    Args:
        records: the normalized connector dump (Gmail / Drive / Calendar).
        user_email: the founder's own address — excluded from the correspondent
            set so a self-cc'd sent mail doesn't whitelist the founder's own
            inbox (matches the mbox lane). Optional; if the connector already
            omits the founder from To/Cc this is a no-op.
        contacts: passed straight to ``build_allowlist`` (resolve a correspondent
            emailed at one address to the phones/handles they also reach the
            founder by). Default ``{}`` keeps the build pure + email-only — the
            cloud routine is headless (no macOS Contacts store), so it must NOT
            trigger a Contacts read; pass an explicit map for the headless
            phone↔email fallback (docs/INGEST-ARCHITECTURE.md). Ignored when an
            ``allowlist`` is injected (the caller already built it).
        gate: the egress/privacy gate the spine sanitizes with (default: a fresh
            ``EgressGate`` — the same classifier every lane uses).
        ingested_at: optional ISO stamp threaded to the spine for events whose
            own timestamp is missing/unparseable.
        allowlist: an OPTIONAL pre-built canonical ``Allowlist`` to gate inbound
            Gmail with, instead of self-building one from this dump's own SENT
            messages. This is the seam the on-ramp (``run.py``) uses to enforce
            the ONE-allowlist rule: when an exported mbox AND a cloud Gmail dump
            are both present, the founder's correspondents are the UNION of who
            they emailed in both, and ``run.py`` builds that one allowlist (via
            the canonical ``ingest.allowlist.build_allowlist``) and injects it
            here so cloud inbound is gated by the SAME set that gates the local
            message lanes. It is still the canonical ``Allowlist`` type — the
            cloud lane never owns a second one. Default ``None`` keeps the
            standalone behavior (``refresh.py``): self-build from this dump's
            SENT correspondents alone.

    Returns:
        A ``CloudProcessResult`` with the ``IngestedCorpus`` + the run accounting.
        Nothing is applied, nothing sent — this is pure processing; the caller
        (``refresh``) runs genesis over the corpus and writes PROPOSALS.
    """
    gate = gate or EgressGate()
    contacts = {} if contacts is None else contacts

    # 1. Gmail SENT-folder filter (the de-spam rule) — reuse the canonical
    #    correspondent harvest + the canonical allowlist. The correspondent set
    #    is ALWAYS harvested from this dump's SENT messages (for the run
    #    accounting + the standalone build); whether we GATE with a freshly built
    #    allowlist or an injected one is the only branch.
    sent = [m for m in records.gmail if m.is_sent]
    inbound = [m for m in records.gmail if not m.is_sent]
    correspondents = correspondents_from_gmail(records.gmail, user_email=user_email)
    allowlist = (
        allowlist
        if allowlist is not None
        else build_allowlist(sorted(correspondents), contacts=contacts)
    )

    admitted_gmail = [m for m in inbound if allowlist.contains(m.sender)]

    # 2. Drive + Calendar are kept as-is (no spam analogue to gate on).
    # 3. Assemble the raw records for the shared spine (the SENT mail itself is
    #    the seed, never re-ingested as an inbound record).
    raw_records = (
        [m.to_raw_record() for m in admitted_gmail]
        + [d.to_raw_record() for d in records.drive]
        + [e.to_raw_record() for e in records.calendar]
    )

    ingested = ingest_records(raw_records, gate=gate, ingested_at=ingested_at)

    return CloudProcessResult(
        corpus=ingested.corpus,
        allowlist=allowlist,
        gmail_sent=len(sent),
        gmail_inbound=len(inbound),
        gmail_admitted=len(admitted_gmail),
        drive_seen=len(records.drive),
        calendar_seen=len(records.calendar),
        kept=ingested.kept,
        dropped_private=ingested.dropped_private,
        dropped_duplicate=ingested.dropped_duplicate,
        dropped_empty=ingested.dropped_empty,
        allowlist_summary=summarize(allowlist),
    )
