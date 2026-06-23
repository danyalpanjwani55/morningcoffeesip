"""Real source adapters — the spine's intake.

Each adapter reads ONE real source the founder actually has and yields raw
records (plain dicts) the pipeline then sanitizes -> dedups -> normalizes into
genesis Events. Adapters do the source-specific parsing; they do NOT sanitize,
dedup, or build Events (that's the pipeline's job — one place, reused by all
adapters). The only contract is ``read() -> Iterable[dict]``.

Two ship in v1, covering the two most universal founder sources:
  * ``LocalFilesAdapter`` — a directory of ``.md`` / ``.txt`` notes.
  * ``EmailAdapter``     — an mbox file or a directory of ``.eml`` messages
    (stdlib ``mailbox`` / ``email``).
"""

from __future__ import annotations

from ingest.adapters.email_source import EmailAdapter
from ingest.adapters.local_files import LocalFilesAdapter

__all__ = ["EmailAdapter", "LocalFilesAdapter"]
