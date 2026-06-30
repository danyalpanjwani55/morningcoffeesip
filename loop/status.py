"""status — a per-agent roll-up the morning boot reads BEFORE anything else.

THE PROBLEM (plain terms): an agent's journal and the skill-delta ledger both
GROW, but at boot nobody wants to re-read a long journal to learn the one thing
that matters — what is this agent working on, what is blocked, what is it going
to do differently, and how much is owed. ``status.md`` is that one-screen
roll-up: derived purely from the journal + the ledger, rebuilt deterministically,
so the morning gate (and the agent's own boot) reads ONE file instead of mining
history.

It is a READER, not a writer of history: it consumes the journal a sibling lane
writes at ``<brain>/wiki/<agent>/journal.md`` (tolerating its absence) and the
skill-delta ledger, and emits a Type-2 / FOR-THE-OPERATOR roll-up. Four parts
(de-welded from the brain's §4.2 status.md):

  * **working-on**  — the LATEST journal entry's ``worked-on``.
  * **open blockers** — derived from the most recent entries (a ``REFUTE`` review
    is the one journal signal that means "a reviewer found something wrong /
    unresolved"); if none, it SAYS so (never an empty silence).
  * **top next-time** — the most recent few ``next-time`` lines (the "one thing
    I'll do differently"), newest first.
  * **counts** — deltas flagged ``ready``-to-graduate (surfaced graduation
    PROPOSALS in the journal) + deltas owed/open for this agent (open rows in the
    skill-delta ledger this agent owns).

Rails (CODE):
  * **Pure + deterministic.** ``build_status`` reads files, returns a string; no
    wall-clock in the output (the "as of" marker is the latest entry's own date),
    so rebuilding from the same inputs yields the SAME file — idempotent.
  * **Never crashes on a missing/partial journal.** No journal -> a clean "no
    history yet" status. An individual malformed entry block is SKIPPED (a boot
    read must not die because one journal block is garbage); the schema's
    never-silent contract still holds where the journal itself is parsed.
  * **Confined writes** under ``<brain>/wiki/`` (asserted, like fold).
  * **Reads the ledger; mutates nothing.** Proposals-only throughout.

No company names / real people / home paths — paths via ``mcs_paths``. Stdlib
only; no network.
"""

from __future__ import annotations

import os
import re
import sys
from typing import List, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import mcs_paths  # noqa: E402

# Sibling loop modules (run with loop/ on sys.path, flat imports like the rest).
import skill_deltas as _sd  # noqa: E402
from journal_schema import (  # noqa: E402
    JournalEntry,
    SchemaError,
    parse_journal_entry,
)
# #4 (adversarial review): the per-agent journal path has ONE owner — the WRITER
# (fold) — so this reader can never silently diverge from where the journal lands.
from fold import agent_journal_path  # noqa: E402,F401  (re-exported below)

# How many recent entries to scan for blockers / next-time lines. NOT a contract
# (the v2 plan explicitly refuses to hardcode a magic window as a contract); just
# a small roll-up budget so the file stays one-screen.
_RECENT_WINDOW = 5
_TOP_NEXT_TIME = 3

# A journal entry header line, so we can split a multi-entry journal into blocks.
_ENTRY_HEADER = re.compile(r"^###\s+J-", re.MULTILINE)


# --------------------------------------------------------------------------- #
# Paths — the per-agent status, beside the journal the sibling lane writes     #
# --------------------------------------------------------------------------- #


def wiki_root(brain_root=None) -> str:
    """The growing per-agent wiki home under the brain: ``<brain>/wiki``."""
    return os.path.join(str(mcs_paths.brain_root(brain_root)), "wiki")


def agent_status_path(agent: str, brain_root=None) -> str:
    """Where this module writes the roll-up: ``<brain>/wiki/<agent>/status.md``."""
    return os.path.join(wiki_root(brain_root), _slug(agent), "status.md")


def _assert_under_wiki_root(path: str, brain_root) -> None:
    root = os.path.realpath(wiki_root(brain_root))
    real = os.path.realpath(path)
    if not (real == root or real.startswith(root + os.sep)):
        raise RuntimeError(f"Refusing write outside wiki root: {path}")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-") or "x"


# --------------------------------------------------------------------------- #
# Reading the journal (tolerant: absent file / partial block never crashes)    #
# --------------------------------------------------------------------------- #


def _read_journal_entries(agent: str, brain_root=None) -> List[JournalEntry]:
    """Parse the agent's journal into entries, OLDEST-first by entry number.

    Tolerant by design (this is a boot read, not the schema's own round-trip
    test): a missing file -> ``[]``; an individual block that fails to parse is
    SKIPPED, not fatal (one garbage block must not break the morning gate). The
    journal is sorted by entry ``n`` so "latest" is well-defined regardless of
    whether the sibling lane appended newest-last or rolled old entries away.
    """
    path = agent_journal_path(agent, brain_root)
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()

    entries: List[JournalEntry] = []
    for block in _split_entry_blocks(text):
        try:
            entries.append(parse_journal_entry(block))
        except SchemaError:
            # A boot read tolerates one bad block; the schema's never-silent rule
            # is enforced where the journal is *written*, not in this consumer.
            continue
    entries.sort(key=lambda e: e.n)
    return entries


def _split_entry_blocks(text: str) -> List[str]:
    """Split a journal file into per-entry blocks on each ``### J-`` header."""
    starts = [m.start() for m in _ENTRY_HEADER.finditer(text)]
    if not starts:
        return []
    bounds = starts + [len(text)]
    return [text[bounds[i]:bounds[i + 1]] for i in range(len(starts))]


# --------------------------------------------------------------------------- #
# Deriving the roll-up parts (pure functions of the parsed entries + ledger)   #
# --------------------------------------------------------------------------- #


def _working_on(entries: List[JournalEntry]) -> List[str]:
    """The LATEST entry's ``worked-on`` (entries are oldest-first)."""
    return list(entries[-1].worked_on) if entries else []


def _open_blockers(entries: List[JournalEntry]) -> List[str]:
    """Blockers derived from the most recent entries.

    The journal has no explicit ``blocker`` field; the one signal that genuinely
    means "a reviewer found something wrong / unresolved" is a ``REVIEW`` of
    ``REFUTE …`` (carry its residual text). We scan the most recent window and
    surface each refuted entry as a blocker line. No REFUTE in the window -> no
    blockers (the caller says so explicitly).
    """
    out: List[str] = []
    for e in reversed(entries[-_RECENT_WINDOW:]):  # newest first
        review = (e.review or "").strip()
        if review.upper().startswith("REFUTE"):
            out.append(f"J-{e.agent}-{e.n:03d} ({e.date}): {review}")
    return out


def _top_next_time(entries: List[JournalEntry]) -> List[str]:
    """The most recent few non-empty ``next-time`` lines, newest first."""
    out: List[str] = []
    for e in reversed(entries):  # newest first
        nt = (e.next_time or "").strip()
        if nt:
            out.append(f"J-{e.agent}-{e.n:03d}: {nt}")
        if len(out) >= _TOP_NEXT_TIME:
            break
    return out


def _ready_count(entries: List[JournalEntry]) -> int:
    """Count journal entries flagged ``ready`` to graduate — the surfaced
    graduation PROPOSALS (a recurrence + CONCUR sets ``graduation`` to ``ready``,
    per Lane C). These are proposals the operator applies; we only surface the
    count."""
    return sum(1 for e in entries if _is_ready(e.graduation))


def _is_ready(graduation: str) -> bool:
    """A ``graduation`` field is 'ready' when it is exactly ``ready`` or names
    ``ready`` as its leading state (e.g. ``ready (recurrence-2x …)``)."""
    g = (graduation or "").strip().lower()
    return g == "ready" or g.startswith("ready ") or g.startswith("ready(")


def _owed_open_count(agent: str, brain_root=None) -> int:
    """Open (``proposed``) skill-deltas this agent OWNS — the backlog owed to it.

    Status comes from the ledger fold (newest-event-wins), exactly as
    ``skill_deltas.open_deltas`` computes it; we filter to this agent's owner
    slug so the count is per-agent."""
    target = _slug(agent)
    return sum(1 for d in _sd.open_deltas(brain_root) if _slug(d.owner) == target)


# --------------------------------------------------------------------------- #
# Render — the Type-2 (operator-facing) one-screen roll-up                      #
# --------------------------------------------------------------------------- #


def build_status(agent: str, brain_root=None) -> str:
    """Build the per-agent ``status.md`` roll-up as a deterministic string.

    Pure read: parses the agent's journal (tolerating absence / partial blocks)
    and reads the skill-delta ledger, then renders working-on / open blockers /
    top next-time / counts. No wall-clock in the output (the "as of" marker is
    the latest entry's own date) -> rebuilding from the same inputs yields the
    SAME string (idempotent). An agent with no journal yet yields a clean "no
    history yet" status, never a crash.
    """
    slug = _slug(agent)
    entries = _read_journal_entries(agent, brain_root)
    ready = _ready_count(entries)
    owed = _owed_open_count(agent, brain_root)

    lines: List[str] = [f"# {slug} — status (roll-up; rebuilt, do not hand-edit)", ""]

    if not entries:
        lines += [
            "## In plain terms",
            "",
            "No journal history yet — this agent has not folded a session. Nothing "
            "to roll up. The counts below read the live ledger regardless.",
            "",
            _counts_block(ready, owed),
        ]
        return "\n".join(lines).rstrip() + "\n"

    latest = entries[-1]
    working = _working_on(entries)
    blockers = _open_blockers(entries)
    next_time = _top_next_time(entries)

    lines += [
        f"_as of J-{latest.agent}-{latest.n:03d} · {latest.date}_",
        "",
        "## Working on",
        "",
    ]
    lines += _bullets_or(working, "(latest entry recorded nothing worked-on)")

    lines += ["", "## Open blockers", ""]
    if blockers:
        lines += [f"- {b}" for b in blockers]
    else:
        lines.append("- none — no refuted/unresolved review in the recent entries.")

    lines += ["", "## Top next-time (what to do differently)", ""]
    lines += _bullets_or(next_time, "(no next-time lines recorded yet)")

    lines += ["", _counts_block(ready, owed)]
    return "\n".join(lines).rstrip() + "\n"


def _counts_block(ready: int, owed: int) -> str:
    return (
        "## Counts\n"
        "\n"
        f"- ready to graduate (proposals — the operator applies): {ready}\n"
        f"- open skill-deltas owed to this agent: {owed}"
    )


def _bullets_or(items: List[str], empty_note: str) -> List[str]:
    return [f"- {x}" for x in items] if items else [f"- {empty_note}"]


# --------------------------------------------------------------------------- #
# Write — confined under the wiki root (the only mutation this module makes)    #
# --------------------------------------------------------------------------- #


def write_status(agent: str, brain_root=None) -> str:
    """Build the roll-up and write it to ``<brain>/wiki/<agent>/status.md``.

    Returns the written path. Idempotent: same journal + ledger -> byte-identical
    file (``build_status`` carries no wall-clock). Confined under the wiki root.
    """
    path = agent_status_path(agent, brain_root)
    _assert_under_wiki_root(path, brain_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = build_status(agent, brain_root)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


__all__ = [
    "build_status",
    "write_status",
    "wiki_root",
    "agent_journal_path",
    "agent_status_path",
]
