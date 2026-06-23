"""The LOCAL ingest lane — the thin Mac sync agent's adapters + entrypoint.

This is the LOCAL half of the architecture split (master spec §6, mirrored in
``docs/INGEST-ARCHITECTURE.md``): only **iMessage + WhatsApp** genuinely need
local code, because their history lives in SQLite files on the founder's own Mac
(behind Full Disk Access — an un-scriptable manual grant), with no usable cloud
API. Everything else (email / Drive / calendar) runs as a CLOUD Claude routine
pointed at the repo, NOT from here.

The sync agent reads those local stores, runs them through the shared ingest
spine (allowlist → sanitize → dedup → normalize), and writes the resulting
genesis Events to the local brain store as proposals — **nothing is sent, nothing
leaves the Mac except sanitized Events the founder controls.**

Lazy-import contract (deliberate — avoids a build-order race)
-------------------------------------------------------------
Importing this PACKAGE must NEVER hard-depend on ``imessage.py`` / ``whatsapp.py``
being present or importable. The accessor functions below import each adapter
*inside the function body*, so:

  * ``import ingest.local`` and ``from ingest.local import sync`` always succeed
    even if one adapter module is missing or mid-edit;
  * a missing/broken adapter degrades to a skipped lane at call time, not an
    ImportError at package-import time.

So callers use the accessors, not top-level adapter imports::

    from ingest.local import imessage_adapter, whatsapp_adapter, local_adapters

This is why the adapters are NOT imported at the top of this file. Stdlib-only.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "imessage_adapter",
    "whatsapp_adapter",
    "local_adapters",
    "available_lanes",
    "LocalAdapterUnavailable",
]


class LocalAdapterUnavailable(ImportError):
    """A local adapter module could not be imported (treat the lane as absent)."""


def imessage_adapter(db_path: Any = None, **kwargs: Any):
    """Construct the iMessage adapter (lazy import inside the function).

    Returns an adapter with a ``read() -> Iterable[dict]`` method, or raises
    ``LocalAdapterUnavailable`` if the adapter module can't be imported (so a
    caller can skip the lane instead of crashing the whole sync).
    """
    cls = _load("imessage", "IMessageAdapter")
    return cls(db_path, **kwargs)


def whatsapp_adapter(store_path: Any = None, **kwargs: Any):
    """Construct the WhatsApp adapter (lazy import inside the function).

    Returns an adapter with a ``read() -> Iterable[dict]`` method, or raises
    ``LocalAdapterUnavailable`` if the adapter module can't be imported.
    """
    cls = _load("whatsapp", "WhatsAppAdapter")
    return cls(store_path, **kwargs)


# The registry the sync entrypoint walks. Each entry is (lane_name -> factory) —
# the factory is called with a per-lane source path (or None for the default).
# Kept as functions (not pre-built instances) so an absent adapter module is a
# skipped lane, never an import-time failure.
_LANE_FACTORIES = {
    "imessage": imessage_adapter,
    "whatsapp": whatsapp_adapter,
}


def available_lanes() -> tuple[str, ...]:
    """The lane names the sync agent knows about (``("imessage", "whatsapp")``)."""
    return tuple(_LANE_FACTORIES)


def local_adapters(
    paths: dict[str, Any] | None = None,
) -> list[tuple[str, Any]]:
    """Build every local adapter that imports cleanly, as ``(lane, adapter)``.

    ``paths`` optionally maps a lane name to its source path (e.g.
    ``{"imessage": "/path/chat.db"}``); a lane absent from the map uses the
    adapter's own default. A lane whose adapter module is missing/broken is
    SILENTLY SKIPPED (the lazy-import guarantee) — it simply doesn't appear in
    the returned list, so ``sync`` degrades gracefully lane-by-lane.
    """
    paths = paths or {}
    out: list[tuple[str, Any]] = []
    for lane, factory in _LANE_FACTORIES.items():
        try:
            out.append((lane, factory(paths.get(lane))))
        except LocalAdapterUnavailable:
            continue
    return out


def _load(module_stem: str, class_name: str):
    """Import ``ingest.local.<module_stem>`` and return ``class_name`` from it.

    The import happens HERE, inside the call, never at package-import time — the
    whole point of this module. An import failure is re-raised as
    ``LocalAdapterUnavailable`` so callers catch one narrow type.
    """
    try:
        module = __import__(
            f"ingest.local.{module_stem}", fromlist=[class_name]
        )
        return getattr(module, class_name)
    except Exception as exc:  # noqa: BLE001 — any import-time failure = lane absent
        raise LocalAdapterUnavailable(
            f"local adapter '{module_stem}.{class_name}' unavailable: {exc}"
        ) from exc
