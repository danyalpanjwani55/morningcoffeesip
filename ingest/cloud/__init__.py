"""The CLOUD ingest lane — the PROCESSING half of the cloud routine.

The cloud half of the architecture split (master spec §6; full picture in
``docs/INGEST-ARCHITECTURE.md`` + ``docs/CLOUD-ROUTINE.md``). It mirrors the live
company brain's ``brain-refresh-cloud`` routine, **de-welded into two pieces** so
the clone-safe portable thing isn't a binary fused to one assistant platform:

  * **AUTH + PULL** — the Claude Gmail / Drive / Calendar **connectors** do this,
    inside the scheduled routine (runtime, lane B). There is NO OAuth / network
    code here — by design (see ``docs/CLOUD-ROUTINE.md`` "the honest status").
  * **PROCESSING** — *this package*. It takes the connector output, already
    pulled, expressed as **normalized connector records** (``schema``), and runs
    it through the SAME proven ingest spine the rest of the system uses
    (allowlist → sanitize → dedup → normalize → genesis), emitting **operator-
    gated proposals** — nothing applied, nothing sent, no git.

The seam between the two is the normalized record (``ingest.cloud.schema``): the
shape the routine maps connector output INTO, and the only thing this code reads.
That makes the processing **code-testable against record fixtures** (this package)
while the connector pull stays an agent/runtime concern (documented setup, not a
faked unit test) — exactly the honesty boundary ``docs/CLOUD-ROUTINE.md`` states.

The one rule the whole system turns on is honored here, NOT re-implemented: the
Gmail SENT-folder filter reuses the canonical
``ingest.adapters.email_source.harvest_sent_correspondents`` and feeds the ONE
``ingest.allowlist.build_allowlist`` — the cloud lane FEEDS the allowlist, it does
not own a second one.

Public surface (flat, like the rest of ``ingest``)::

    from ingest.cloud import (
        GmailMessage, DriveDoc, CalendarEvent,        # the normalized records
        normalized_records_from_json,                  # parse a connector dump
        process_cloud_records, CloudProcessResult,     # the processing
    )

Stdlib-only; no network, no OAuth, no writes (the entrypoint ``refresh`` writes
the proposals file, confined like the genesis pipeline).
"""

from __future__ import annotations

from ingest.cloud.schema import (
    CalendarEvent,
    DriveDoc,
    GmailMessage,
    NormalizedRecords,
    normalized_records_from_json,
    normalized_records_from_obj,
)
from ingest.cloud.process import (
    CloudProcessResult,
    correspondents_from_gmail,
    process_cloud_records,
)

__all__ = [
    "GmailMessage",
    "DriveDoc",
    "CalendarEvent",
    "NormalizedRecords",
    "normalized_records_from_json",
    "normalized_records_from_obj",
    "CloudProcessResult",
    "correspondents_from_gmail",
    "process_cloud_records",
]
