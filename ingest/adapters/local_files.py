"""LocalFilesAdapter — ingest a directory of ``.md`` / ``.txt`` notes.

The most universal founder source: a folder of plain notes (meeting notes,
decisions, a working journal). Each file becomes one raw record; the file's path
is the stable ``source_id`` (so re-ingesting the same file dedups), and the
file's mtime is the ``observed_at`` (so genesis can order/window by recency).

Stdlib-only. The only I/O is reading files under the directory you give it. The
root is resolved through ``mcs_paths`` so a default ('<brain_root>/sources/notes')
works on any machine, while an explicit ``root=`` overrides for tests. No writes.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import mcs_paths  # noqa: E402

# The note extensions we ingest. (.markdown is the long form of .md.)
_DEFAULT_EXTENSIONS = (".md", ".txt", ".markdown")


class LocalFilesAdapter:
    """Yield one raw record per note file under a directory (recursive).

    Args:
        root: the directory to read. Default: ``<brain_root>/sources/notes`` via
            ``mcs_paths`` (so a clone works without configuration). Not required
            to exist — a missing/empty dir yields nothing.
        extensions: which file suffixes count as notes.
        kind: the genesis ``kind`` stamped on every record (the source label).
    """

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        extensions: Iterable[str] = _DEFAULT_EXTENSIONS,
        kind: str = "note",
    ):
        self.root = (
            mcs_paths._norm(root)
            if root is not None
            else mcs_paths.brain_root() / "sources" / "notes"
        )
        self.extensions = tuple(e.lower() for e in extensions)
        self.kind = kind

    def read(self) -> Iterator[dict[str, Any]]:
        if not self.root.is_dir():
            return
        # Deterministic order: sorted by path so a genesis run is reproducible.
        for path in sorted(self._iter_files(), key=lambda p: str(p)):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                # Skip an unreadable / non-UTF-8 file rather than crash the run.
                continue
            rel = self._safe_rel(path)
            yield {
                "kind": self.kind,
                "source_type": self.kind,
                "source_id": rel,
                "locator": "",
                "text": text,
                "title": path.stem,
                "observed_at": _mtime_iso_utc(path),
                "participants": [],
                "meta": {"adapter": "local_files"},
            }

    def _iter_files(self) -> Iterable[Path]:
        for path in self.root.rglob("*"):
            if path.is_file() and path.suffix.lower() in self.extensions:
                yield path

    def _safe_rel(self, path: Path) -> str:
        """A stable, machine-independent source_id: the path RELATIVE to root
        (so two clones at different absolute paths produce the same id, and no
        home path leaks into the corpus)."""
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return path.name


def _mtime_iso_utc(path: Path) -> str:
    ts = path.stat().st_mtime
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
