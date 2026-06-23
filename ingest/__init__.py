"""MorningCoffeeSip ingest spine — the genesis engine's fuel.

A generic, stdlib-first ``sanitize -> normalize -> dedup`` pipeline that turns a
founder's real raw sources into the genesis ``Event`` objects ``run_genesis``
consumes (so genesis stops running only on a synthetic in-memory sample).

This is the de-welded descendant of the live company brain's source-adapter
stack (``ingest_source_normalization`` + ``ingest_dedupe`` +
``ingest_privacy_gate``): the genuinely reusable ~75% — the
strip-secrets/coerce-to-a-canonical-shape/dedup-once spirit — with EVERY
company-specific thing removed (no company source taxonomy, no real people, no
machine paths, no bespoke artifact contract). The privacy gate is the repo-root
reusable ``mcs_egress`` classifier, not a re-implementation.

Public surface (flat imports, like ``genesis/``):

    from ingest import (
        sanitize_record, normalize_record, dedupe_key,
        ingest_records, IngestedCorpus,
        LocalFilesAdapter, EmailAdapter,
    )

Everything here is pure: no network, and the only writes are an adapter reading
files you point it at. The pipeline emits genesis ``Event`` objects and an
``IngestedCorpus`` whose ``events_since`` lets ``run_genesis`` consume it
directly.
"""

from __future__ import annotations

from ingest.adapters import EmailAdapter, LocalFilesAdapter
from ingest.corpus import IngestedCorpus
from ingest.dedupe import dedupe_key, stable_digest
from ingest.normalize import RawRecord, normalize_record
from ingest.pipeline import IngestResult, ingest_records, ingest_sources
from ingest.sanitize import SanitizeDecision, sanitize_record

__all__ = [
    "EmailAdapter",
    "LocalFilesAdapter",
    "IngestedCorpus",
    "dedupe_key",
    "stable_digest",
    "RawRecord",
    "normalize_record",
    "IngestResult",
    "ingest_records",
    "ingest_sources",
    "SanitizeDecision",
    "sanitize_record",
]
