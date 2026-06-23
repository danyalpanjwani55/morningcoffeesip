"""fold — compound session learnings / pulses into the agent wikis so memory GROWS.

THE PROBLEM (plain terms): a pulse (a session's written record) is write-only
memory until it is *folded* — knowledge that never reaches a wiki never reaches
an agent's boot. So pulses pile up and the agents never get smarter. This module
is the fold engine: sweep the unfolded pulses, route each to the agent(s) it
teaches, fold it into that agent's wiki (memory grows), and restamp the pulse
``folded:`` so it's never re-swept.

THE TWO TIERS (de-welded verbatim from the live fold skill, step 2):
  * **pointer** — a transient work record -> ONE dated line appended to the
    owning agent's ``log.md`` (append-only; the wiki grows by accretion). Most
    pulses are pointers.
  * **fold** — durable domain knowledge -> a CITED wiki page built via the
    genesis ``agent_wiki_builder`` (reused, not reinvented), under the brain.

CHRONOLOGY (the anti-stale guarantee, step 1b): the sweep is sorted OLDEST
FIRST, because a later pulse may correct an earlier one. Each agent's log grows
in event order; nothing folds a stale item over a newer correction.

Memory GROWTH is the whole point: ``<brain>/wiki/<agent>/log.md`` is append-only
and accumulates across runs, so an agent that boots its wiki tomorrow knows what
its sessions learned today. The genesis builder writes the heavier cited pages
for fold-tier knowledge.

Rails (CODE):
  * **Idempotent on the ``folded:`` stamp.** An already-stamped pulse is skipped
    (no double-fold). Restamping is the only mutation to a source pulse.
  * **Confined writes** under ``<brain>/wiki/`` (asserted).
  * **Reuse the genesis builder** for fold-tier cited pages (its egress +
    anchor-or-drop rails come along for free).
  * **Proposes/records; never sends, never auto-applies a doctrine change.**

No company names / real people / home paths — paths via ``mcs_paths``. Stdlib
only; no network. ALL model judgment (where used) routes through the injected
genesis ``LLM``/``EgressGate``.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Iterable

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
# genesis/ holds agent_wiki_builder + genesis_contracts (flat imports there).
_GENESIS = os.path.join(_REPO_ROOT, "genesis")
if _GENESIS not in sys.path:
    sys.path.insert(0, _GENESIS)

import mcs_paths  # noqa: E402

# Reuse the genesis builder + contracts (do NOT reinvent the cited-wiki machinery).
import agent_wiki_builder as awb  # noqa: E402
from genesis_contracts import (  # noqa: E402
    Anchor,
    Corpus,
    EgressGate,
    InMemoryCorpus,
    LLM,
)

# Frontmatter key that marks a pulse as folded (the live convention).
_FOLDED_KEY = "folded"
# Tiers
POINTER = "pointer"
FOLD = "fold"


# --------------------------------------------------------------------------- #
# Paths — the brain-rooted, GROWING wiki (persists across runs)                #
# --------------------------------------------------------------------------- #


def wiki_root(brain_root=None) -> str:
    """The growing per-agent wiki home under the brain: ``<brain>/wiki``."""
    return os.path.join(str(mcs_paths.brain_root(brain_root)), "wiki")


def agent_log_path(agent: str, brain_root=None) -> str:
    """An agent's append-only growth log: ``<brain>/wiki/<agent>/log.md``."""
    return os.path.join(wiki_root(brain_root), _slug(agent), "log.md")


def _assert_under_wiki_root(path: str, brain_root) -> None:
    root = os.path.realpath(wiki_root(brain_root))
    real = os.path.realpath(path)
    if not (real == root or real.startswith(root + os.sep)):
        raise RuntimeError(f"Refusing write outside wiki root: {path}")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-") or "x"


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


# --------------------------------------------------------------------------- #
# Pulse model + frontmatter parsing                                           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Pulse:
    """One session/pulse record to fold.

    A pulse names the agent(s) it teaches, a tier (pointer | fold), a date (for
    chronological ordering), a one-line lesson, and its source anchors. In a live
    deployment these come from parsing pulse files; the dataclass keeps the engine
    deterministic + testable.
    """

    pulse_id: str
    date: str                          # ISO date (chronological sort key)
    agents: tuple[str, ...]            # owning agent(s)
    tier: str                          # POINTER | FOLD
    lesson: str                        # the one-line learning
    anchors: tuple[Anchor, ...] = ()   # source citations (>=1 for FOLD tier)
    path: str | None = None            # the source pulse file (restamped if given)


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def is_folded(pulse_file_text: str) -> bool:
    """True iff the pulse's frontmatter carries a non-empty, non-``pending``
    ``folded:`` value (the live backlog rule: absent or ``pending`` => unfolded)."""
    m = _FM_RE.match(pulse_file_text)
    if not m:
        return False
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        if k.strip() == _FOLDED_KEY:
            v = v.strip()
            return bool(v) and v.lower() != "pending" and v not in ("[]", "[ ]")
    return False


def restamp_folded(pulse_file_text: str, agents: Iterable[str], date: str) -> str:
    """Return the pulse text with ``folded: [agents] (date)`` set in frontmatter
    (added if absent, replaced if present). The ONLY mutation fold makes to a
    source pulse — so the next sweep skips it (idempotent)."""
    stamp = f"{_FOLDED_KEY}: [{', '.join(_slug(a) for a in agents)}] ({date})"
    m = _FM_RE.match(pulse_file_text)
    if not m:
        # no frontmatter — prepend a minimal block
        return f"---\n{stamp}\n---\n\n" + pulse_file_text
    fm = m.group(1)
    lines = fm.splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.split(":", 1)[0].strip() == _FOLDED_KEY:
            lines[i] = stamp
            replaced = True
            break
    if not replaced:
        lines.append(stamp)
    new_fm = "\n".join(lines)
    return pulse_file_text[: m.start(1)] + new_fm + pulse_file_text[m.end(1):]


# --------------------------------------------------------------------------- #
# Result object                                                               #
# --------------------------------------------------------------------------- #


@dataclass
class FoldResult:
    """What a fold run did — what grew, what was pointer vs fold, what was skipped."""

    folded_pulse_ids: list[str] = field(default_factory=list)
    skipped_already_folded: list[str] = field(default_factory=list)
    agent_log_lines_added: dict[str, int] = field(default_factory=dict)
    wikis_built: list[str] = field(default_factory=list)   # agent slugs w/ a cited page
    restamped_files: list[str] = field(default_factory=list)

    @property
    def total_folded(self) -> int:
        return len(self.folded_pulse_ids)


# --------------------------------------------------------------------------- #
# The fold engine                                                             #
# --------------------------------------------------------------------------- #


def fold(
    pulses: Iterable[Pulse],
    *,
    corpus: Corpus | None = None,
    llm: LLM | None = None,
    egress: EgressGate | None = None,
    brain_root=None,
    today: str = "",
) -> FoldResult:
    """Fold a batch of pulses into the growing agent wikis.

    Procedure (de-welded from the live fold skill):
      1. **Sort chronologically, OLDEST FIRST** (anti-stale: a later pulse may
         correct an earlier one; each agent's log grows in event order).
      2. **Route** each pulse to its owning agent(s).
      3. **Pointer tier** -> append ONE dated line to ``<brain>/wiki/<agent>/log.md``
         (the wiki grows by accretion).
      4. **Fold tier** -> ALSO build a CITED wiki page via the genesis
         ``agent_wiki_builder`` (reused), grounded in the corpus + the pulse's
         anchors. A fold-tier pulse with no anchors is DROPPED to pointer (cite
         >=1 — verify-before-relay).
      5. **Restamp** each processed pulse ``folded:`` (idempotent; the only source
         mutation). A pulse whose file is already folded is SKIPPED.

    ``corpus``/``llm``/``egress`` are only needed when fold-tier pulses are
    present (the genesis builder requires them); pointer-only batches don't.
    """
    date = today or _today()
    result = FoldResult()

    ordered = sorted(pulses, key=lambda p: (p.date, p.pulse_id))
    for pulse in ordered:
        # idempotency: if the source file is already folded, skip it entirely.
        if pulse.path and os.path.isfile(pulse.path):
            with open(pulse.path, "r", encoding="utf-8") as fh:
                text = fh.read()
            if is_folded(text):
                result.skipped_already_folded.append(pulse.pulse_id)
                continue
        else:
            text = None

        tier = pulse.tier
        # A fold-tier pulse with no anchors can't ground a cited page -> pointer.
        if tier == FOLD and not pulse.anchors:
            tier = POINTER

        for agent in pulse.agents:
            # 3. pointer growth — every pulse (pointer or fold) lands a log line.
            _append_log_line(agent, pulse, date, brain_root)
            result.agent_log_lines_added[_slug(agent)] = (
                result.agent_log_lines_added.get(_slug(agent), 0) + 1
            )

            # 4. fold growth — durable knowledge gets a CITED wiki page (reuse).
            if tier == FOLD:
                _build_cited_page(
                    agent, pulse, corpus=corpus, llm=llm, egress=egress,
                    brain_root=brain_root, date=date,
                )
                if _slug(agent) not in result.wikis_built:
                    result.wikis_built.append(_slug(agent))

        # 5. restamp the source pulse (idempotent; only source mutation).
        if text is not None and pulse.path:
            new_text = restamp_folded(text, pulse.agents, date)
            with open(pulse.path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
            result.restamped_files.append(pulse.path)

        result.folded_pulse_ids.append(pulse.pulse_id)

    return result


def _append_log_line(agent: str, pulse: Pulse, date: str, brain_root) -> None:
    """Append ONE dated line to the agent's append-only growth log (creating the
    log with a header on first write). This is how the wiki GROWS by accretion —
    newest at the bottom, never rewritten."""
    path = agent_log_path(agent, brain_root)
    _assert_under_wiki_root(path, brain_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.isfile(path)
    anc = ", ".join(awb._anchor_md(a) for a in pulse.anchors) or "(no anchor)"
    line = (
        f"- [{pulse.date}] ({pulse.tier}) {pulse.lesson.strip()} "
        f"— folded {date} · src `{pulse.pulse_id}` · {anc}"
    )
    with open(path, "a", encoding="utf-8") as fh:
        if new:
            fh.write(
                f"# {_slug(agent)} — wiki log\n\n"
                "Append-only. One entry per folded pulse; newest at bottom. "
                "This is how the agent's memory GROWS across sessions.\n\n"
            )
        fh.write(line + "\n")


def _build_cited_page(
    agent: str,
    pulse: Pulse,
    *,
    corpus: Corpus | None,
    llm: LLM | None,
    egress: EgressGate | None,
    brain_root,
    date: str,
) -> None:
    """Build/refresh a CITED wiki page for a fold-tier pulse by REUSING the
    genesis ``agent_wiki_builder`` — its anchor-or-drop + egress + confined-write
    rails come along for free. The builder writes under its own confinement root
    (``genesis/out/wiki/``); we mirror the produced cited source page into the
    brain-rooted growing wiki so the agent's persistent memory holds it.

    Requires corpus/llm/egress (the builder's contract); a fold-tier pulse should
    not reach here without them (the engine drops to pointer when anchors are
    absent, and the caller supplies the trio when fold-tier pulses are present).
    """
    if corpus is None or llm is None or egress is None:
        # Defensive: without the builder's inputs we cannot cite a page. Fall
        # back to the pointer line already written — never fabricate an uncited
        # page (verify-before-relay).
        return

    built = awb.build_agent_wiki(
        agent,
        pulse.anchors,
        corpus,
        llm,
        egress,
        domain=_slug(agent),
        proposal_status="folded",
        today=date,
    )
    # Mirror the builder's cited source pages into the brain-rooted growing wiki
    # (the genesis out/ tree is regenerated scratch; the brain wiki persists).
    dest_dir = os.path.join(wiki_root(brain_root), _slug(agent), "sources")
    _assert_under_wiki_root(dest_dir, brain_root)
    os.makedirs(dest_dir, exist_ok=True)
    for src_page in built.source_pages:
        base = os.path.basename(src_page)
        dest = os.path.join(dest_dir, base)
        _assert_under_wiki_root(dest, brain_root)
        with open(src_page, "r", encoding="utf-8") as fh:
            page = fh.read()
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(page)


# --------------------------------------------------------------------------- #
# Convenience: a from-corpus helper so a caller need not build InMemoryCorpus  #
# --------------------------------------------------------------------------- #


def corpus_from_events(events: Iterable) -> InMemoryCorpus:
    """Wrap genesis ``Event``s into the in-memory corpus the builder consumes."""
    return InMemoryCorpus(list(events))


__all__ = [
    "Pulse", "FoldResult", "POINTER", "FOLD",
    "wiki_root", "agent_log_path",
    "is_folded", "restamp_folded",
    "fold", "corpus_from_events",
]
