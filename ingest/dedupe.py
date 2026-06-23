"""Dedup — a stable, privacy-preserving idempotency key for one ingest record.

De-welded from the company brain's ``ingest_dedupe.py``. What carried over (the
genuinely reusable ~75%):

  * ``stable_digest`` — a deterministic sha256 over a *canonicalized* value that
    DROPS raw-text fields, so message/body text never leaks into a key or index
    (``RAW_TEXT_FIELD_NAMES`` + ``_canonicalize``, verbatim spirit).
  * the "prefer an explicit id, else hash safe content" key strategy
    (``_scoped_hash_key``).

What was deleted (company-specific, not reusable): the per-source key rule table
(gmail / imessage / slack / meet / otter / drive ...) that RAISED on any source
type the company didn't enumerate; the artifact-contract coupling; and all the
local index/manifest file-writing (``write_artifact_once`` et al). Genesis does
its own pillar-draft writes; the spine only needs the key.

The key here is source-AGNOSTIC by construction: any adapter can ingest by
supplying a ``source`` label + a stable id, and an unknown source just works —
no enumerated allowlist to update. Stdlib-only; no network, no file I/O.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

DEFAULT_DIGEST_LENGTH = 32

# Field names whose VALUES are raw human text. Their values are never folded
# into a dedupe key/digest (privacy-preserving idempotency — a key must not
# carry a message body). Lifted from the brain's ingest_dedupe.
RAW_TEXT_FIELD_NAMES = frozenset(
    {
        "body",
        "body_text",
        "description",
        "html",
        "message",
        "message_text",
        "raw",
        "raw_text",
        "snippet",
        "text",
        "transcript",
    }
)


def stable_digest(value: Any, *, length: int = DEFAULT_DIGEST_LENGTH) -> str:
    """A deterministic sha256 hex prefix over a canonicalized ``value``.

    Canonicalization sorts dict keys, normalizes Paths to strings, and DROPS any
    key in ``RAW_TEXT_FIELD_NAMES`` at every depth — so the same logical record
    digests identically across runs, and raw text never enters the digest.
    """
    material = json.dumps(
        _canonicalize(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:length]


def content_hash_for_text(text: str) -> str:
    """``sha256:<hex>`` over UTF-8 text (the fallback content fingerprint)."""
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def dedupe_key(
    source: str,
    *,
    source_id: str | None = None,
    text: str = "",
    extra: dict[str, Any] | None = None,
) -> str:
    """A stable, privacy-preserving dedupe key for one record.

    Strategy (source-agnostic — no enumerated source allowlist):
      1. if a stable ``source_id`` is given -> ``<source>:id:sha256-<digest(id)>``
      2. else -> hash the *content fingerprint* (sha256 of the text) plus any
         non-raw ``extra`` metadata -> ``<source>:content:sha256-<digest>``

    The same item therefore ingests exactly once: a re-seen id collides on (1);
    an id-less item with identical text collides on (2). Raw text is fed only
    through ``content_hash_for_text`` (a one-way digest), never embedded.
    """
    src = _slug(source) or "source"
    if source_id and str(source_id).strip():
        return _scoped_hash_key(src, "id", str(source_id).strip())
    material: dict[str, Any] = {"content_hash": content_hash_for_text(text)}
    if extra:
        # _canonicalize drops raw-text fields, so passing a whole record is safe.
        material.update(extra)
    return _scoped_hash_key(src, "content", material)


def _scoped_hash_key(
    source: str, method: str, material: Any, *, length: int = DEFAULT_DIGEST_LENGTH
) -> str:
    return f"{source}:{method}:sha256-{stable_digest(material, length=length)}"


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key).lower() not in RAW_TEXT_FIELD_NAMES
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, set):
        return sorted(_canonicalize(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    return value


def _slug(raw: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in str(raw).strip().lower()).strip("-")
