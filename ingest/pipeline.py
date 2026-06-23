"""Pipeline — the spine that wires ``sanitize -> normalize -> dedup`` into a
genesis ``IngestedCorpus``.

De-welded from the company brain's source-adapter orchestration
(``build_fixture_packet`` walking records through normalize + dedupe + the
privacy gate). The reusable shape: for each raw record, (1) screen it
(``sanitize_record`` — drop private), (2) compute a stable key
(``dedupe_key`` — drop a duplicate), (3) coerce it to a genesis ``Event``
(``normalize_record``), and collect the survivors into an ``IngestedCorpus`` that
``run_genesis`` consumes directly.

The pipeline is deterministic and side-effect-free (the only I/O is whatever an
adapter does when *you* hand it a directory). It returns an ``IngestResult`` that
keeps the corpus alongside per-record dispositions, so a caller can see exactly
what was kept, dropped-private, or dropped-duplicate — without ever surfacing the
private content itself (only a reason code).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from mcs_egress import EgressGate

from ingest.corpus import IngestedCorpus
from ingest.dedupe import dedupe_key
from ingest.normalize import RawRecord, normalize_record
from ingest.sanitize import sanitize_record

# Field aliases the pipeline uses to find a record's stable source id + text for
# the dedupe key (a superset of normalize's, kept local so the two stay
# independent).
_SOURCE_ALIASES = ("kind", "source_type", "source", "type")
_SOURCE_ID_ALIASES = ("source_id", "id", "message_id", "file_id", "path", "dedupe_key")
_TEXT_ALIASES = ("text", "body", "content", "message", "snippet")


@dataclass
class IngestDisposition:
    """What happened to one raw record. ``reason`` is a machine code; no record
    ever carries raw private content into this struct (only the reason)."""

    status: str          # "kept" | "dropped_private" | "dropped_duplicate" | "dropped_empty"
    dedupe_key: str
    reason: str = ""


@dataclass
class IngestResult:
    """The corpus plus an accounting of every input record."""

    corpus: IngestedCorpus
    dispositions: list[IngestDisposition] = field(default_factory=list)

    @property
    def kept(self) -> int:
        return sum(1 for d in self.dispositions if d.status == "kept")

    @property
    def dropped_private(self) -> int:
        return sum(1 for d in self.dispositions if d.status == "dropped_private")

    @property
    def dropped_duplicate(self) -> int:
        return sum(1 for d in self.dispositions if d.status == "dropped_duplicate")

    @property
    def dropped_empty(self) -> int:
        return sum(1 for d in self.dispositions if d.status == "dropped_empty")


def ingest_records(
    records: Iterable[Mapping[str, Any] | RawRecord],
    *,
    gate: EgressGate | None = None,
    ingested_at: str | None = None,
) -> IngestResult:
    """Run the full spine over raw records and return an ``IngestResult``.

    Order of operations per record (the de-welded spine):
      1. sanitize  -> drop if private (fail-closed),
      2. dedupe    -> drop if the stable key was already seen,
      3. normalize -> coerce to a genesis Event and keep.

    Sanitize runs BEFORE dedupe so a private record is never even keyed/counted
    as a duplicate; dedupe runs before normalize so we don't build an Event we'd
    discard. ``event_id`` is set to the dedupe key so the Event id is stable +
    collision-safe across runs.
    """
    gate = gate or EgressGate()
    events = []
    dispositions: list[IngestDisposition] = []
    seen_keys: set[str] = set()

    for record in records:
        data = record.as_dict() if isinstance(record, RawRecord) else dict(record)

        source = _first_str(data, _SOURCE_ALIASES) or "source"
        source_id = _first_str(data, _SOURCE_ID_ALIASES)
        text = _first_str(data, _TEXT_ALIASES) or ""
        key = dedupe_key(source, source_id=source_id, text=text, extra=data)

        # Substance gate: an empty BODY carries nothing to ingest (e.g. an
        # HTML-only email whose only screenable text was its subject). Drop it
        # before sanitize — there's nothing to anchor a claim on. (Distinct from
        # sanitize, which is about privacy, not substance.)
        if not text:
            dispositions.append(
                IngestDisposition(status="dropped_empty", dedupe_key=key, reason="no_body_text")
            )
            continue

        decision = sanitize_record(data, gate=gate)
        if not decision.allowed:
            dispositions.append(
                IngestDisposition(status="dropped_private", dedupe_key=key, reason=decision.reason)
            )
            continue

        if key in seen_keys:
            dispositions.append(
                IngestDisposition(status="dropped_duplicate", dedupe_key=key, reason="dedupe_key_seen")
            )
            continue
        seen_keys.add(key)

        # Stamp the dedupe key as the Event id (stable, collision-safe).
        data["event_id"] = key
        event = normalize_record(data, ingested_at=ingested_at)
        events.append(event)
        dispositions.append(IngestDisposition(status="kept", dedupe_key=key))

    return IngestResult(corpus=IngestedCorpus(events), dispositions=dispositions)


def ingest_sources(
    adapters: Iterable[Any],
    *,
    gate: EgressGate | None = None,
    ingested_at: str | None = None,
) -> IngestResult:
    """Convenience: pull raw records from one or more adapters (anything with a
    ``read() -> Iterable[record]`` method) and ingest them as one corpus."""
    def _all() -> Iterable[Any]:
        for adapter in adapters:
            yield from adapter.read()

    return ingest_records(_all(), gate=gate, ingested_at=ingested_at)


def _first_str(data: Mapping[str, Any], keys) -> str | None:
    for k in keys:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if v not in (None, "", [], ()) and not isinstance(v, (list, tuple, dict, set)):
            return str(v).strip()
    return None
