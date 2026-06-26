"""recall_health — the oversize-page canary for the knowledge-health scan.

THE PROBLEM (plain terms): the brain's per-agent wikis grow by accretion. A
page that quietly swells past the bounded-write cap stops being navigable — it
becomes an unreadable pile, and nobody notices until it's already a mess. The
``morning`` skill's recall-health scan watches for orphaned and stale pages, but
it has NO detector for a page that has simply grown too big. This module is that
missing canary: walk the wiki markdown tree and flag any page whose line count
exceeds the same bounded-write cap genesis already enforces on a freshly written
page (``genesis_pipeline._MAX_DRAFT_LINES``).

SURFACE ONLY — never auto-fix. This module is a *detector*. ``scan_oversize_pages``
returns findings; it NEVER edits, splits, trims, or rewrites a page. Splitting an
overgrown page is a judgement call (which bullets are stale, what the child page
is named) that belongs to a human at the gate or to the existing
supersede-with-archive machinery — not to a blind line-count check. Honoring the
knowledge-hygiene rule ("never silently delete"), the canary only *reports*.

HOW MORNING USES IT: the ``morning`` skill's step-0 RECALL-HEALTH SCAN (the
``(c,d,e)`` block — fold-backlog, routing-map integrity, orphan/staleness sweep)
calls ``scan_oversize_pages(wiki_root())`` and surfaces the returned findings at
the gate alongside the existing orphan/staleness counts. An oversize page is a
SURFACE-severity finding (a hygiene backlog to drain), not a HALT — one summary
line, worst offenders named. This module is a clean importable function so the
morning gate CAN call it; the wiring into ``skills/morning/SKILL.md`` is owned by
the steering-skill lane, not this module.

THE CAP IS SHARED, NOT REINVENTED: the threshold is genesis's bounded-write rule
``genesis_pipeline._MAX_DRAFT_LINES`` (synthesis-writer rule 4). We import it so a
page is judged "oversize" by exactly the same number a fresh page is bounded to —
if that rule ever changes, the canary moves with it. We do NOT hardcode the value.

No company names / real people / home paths. Stdlib only; no network; read-only.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Iterator

# genesis/ holds genesis_pipeline (flat imports there). Mirror fold.py's path
# bootstrap so the shared bounded-write cap is importable from this module.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GENESIS = os.path.join(_REPO_ROOT, "genesis")
if _GENESIS not in sys.path:
    sys.path.insert(0, _GENESIS)

# REUSE the genesis bounded-write cap — do NOT hardcode 200. A page is oversize
# by exactly the number a freshly written page is bounded to.
from genesis_pipeline import _MAX_DRAFT_LINES  # noqa: E402


@dataclass(frozen=True)
class OversizeFinding:
    """One wiki page that has grown past the bounded-write cap.

    Pure data — a *report*, not an action. ``path`` is the offending page,
    ``lines`` its current line count, ``cap`` the threshold it exceeded
    (carried so the surface line is self-explaining without re-reading config).
    """

    path: str
    lines: int
    cap: int

    @property
    def over_by(self) -> int:
        """How many lines past the cap — for ranking worst offenders first."""
        return self.lines - self.cap


def _iter_markdown(wiki_root: str) -> Iterator[str]:
    """Yield every ``.md`` file under ``wiki_root`` (sorted, deterministic)."""
    for dirpath, dirnames, filenames in os.walk(wiki_root):
        dirnames.sort()
        for name in sorted(filenames):
            if name.endswith(".md"):
                yield os.path.join(dirpath, name)


def _line_count(path: str) -> int:
    """Number of lines in a file (read-only). Counts the same way ``str``
    .splitlines() does on the bounded-write side, so the comparison is honest
    against the cap genesis enforces."""
    with open(path, encoding="utf-8") as fh:
        return len(fh.read().splitlines())


def scan_oversize_pages(
    wiki_root: str, *, cap: int = _MAX_DRAFT_LINES
) -> list[OversizeFinding]:
    """Walk a wiki markdown tree; flag every page whose line count exceeds ``cap``.

    A page/index whose body runs longer than the bounded-write cap is the canary
    for drift: it has stopped being navigable. Returns the findings (worst
    offender first); SURFACE ONLY — never edits or splits a page.

    Args:
        wiki_root: the root of the wiki markdown tree (e.g. ``fold.wiki_root()``
            -> ``<brain>/wiki``). A path that does not exist yields ``[]`` (a
            brain with no wiki yet is healthy, not broken).
        cap: the line threshold; defaults to the SHARED genesis bounded-write
            cap ``genesis_pipeline._MAX_DRAFT_LINES`` (not a literal). Overridable
            only so callers/tests can pin a value.

    Returns:
        ``list[OversizeFinding]`` — strictly-over-cap pages, ranked most-over
        first. Empty when every page is within the cap (or the tree is absent).
    """
    if not os.path.isdir(wiki_root):
        return []
    findings = [
        OversizeFinding(path=path, lines=lines, cap=cap)
        for path in _iter_markdown(wiki_root)
        if (lines := _line_count(path)) > cap
    ]
    findings.sort(key=lambda f: (-f.over_by, f.path))
    return findings


__all__ = ["OversizeFinding", "scan_oversize_pages"]
