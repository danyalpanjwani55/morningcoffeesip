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

# Reuse the ONE canonical journal/concept formats (the fold writes them; the
# wiki builder, status, and router read them — never re-define the shapes).
from journal_schema import (  # noqa: E402
    ConceptState,
    JournalEntry,
    parse_concept_state,
    parse_journal_entry,
    render_concept_state,
    render_journal_entry,
)

# Frontmatter key that marks a pulse as folded (the live convention).
_FOLDED_KEY = "folded"
# Tiers
POINTER = "pointer"
FOLD = "fold"

# How many entries the HOT journal holds before the oldest rolls to the archive.
# Deliberately NOT a doctrine contract (the port plan's Musk-cut: "don't hardcode
# a magic number as a contract") — just a sane cap so the hot file stays readable;
# rolled entries stay grep-reachable in journal-archive/ (archive, never delete).
_HOT_JOURNAL_MAX = 14

# A review "concurs" when an independent twin agreed (the recurrence→graduation
# gate fires only on CONCUR, never on REFUTE/n/a). The reviewer call itself is
# operator/loop-driven; this only reads the recorded verdict.
_CONCUR = "CONCUR"
# A symptom seen this many times (hot ∪ archive) WITH a CONCUR review graduates.
_GRADUATION_RECURRENCE = 2
GRADUATION_READY = "ready"


# --------------------------------------------------------------------------- #
# Paths — the brain-rooted, GROWING wiki (persists across runs)                #
# --------------------------------------------------------------------------- #


def wiki_root(brain_root=None) -> str:
    """The growing per-agent wiki home under the brain: ``<brain>/wiki``."""
    return os.path.join(str(mcs_paths.brain_root(brain_root)), "wiki")


def agent_log_path(agent: str, brain_root=None) -> str:
    """An agent's append-only growth log: ``<brain>/wiki/<agent>/log.md``."""
    return os.path.join(wiki_root(brain_root), _slug(agent), "log.md")


def agent_journal_path(agent: str, brain_root=None) -> str:
    """An agent's HOT journal (newest learnings): ``<brain>/wiki/<agent>/journal.md``.

    Append-only; the oldest entry rolls to ``journal-archive/`` once the hot file
    is large, so the agent's whole history stays grep-reachable (never deleted)."""
    return os.path.join(wiki_root(brain_root), _slug(agent), "journal.md")


def agent_journal_archive_dir(agent: str, brain_root=None) -> str:
    """Where rolled-off journal entries live: ``<brain>/wiki/<agent>/journal-archive``.

    Archive-don't-delete: entries that age out of the hot journal land here so
    recurrence detection (and a human grep) still reach the full history."""
    return os.path.join(wiki_root(brain_root), _slug(agent), "journal-archive")


def agent_concept_path(agent: str, concept_slug: str, brain_root=None) -> str:
    """An agent's concept STATE file: ``<brain>/wiki/<agent>/concepts/<slug>.md``.

    The fold restamps (appends a dated ``history:`` line to) any of these whose
    concept a folded pulse touched — never overwriting a prior history line."""
    return os.path.join(
        wiki_root(brain_root), _slug(agent), "concepts", f"{_slug(concept_slug)}.md"
    )


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

    # --- Learning-loop v2 journal fields (all optional; a plain pointer pulse
    # carries none of these and still writes a minimal journal entry). The fold
    # folds these into a JournalEntry per touched agent (the merged
    # lesson -> proposed-delta -> review -> next-time -> concept-touched chain). ---
    title: str = ""                    # journal entry title (defaults to the lesson)
    worked_on: tuple[str, ...] = ()    # what the session worked on
    understood: tuple[str, ...] = ()   # what it newly understood
    symptom: str = ""                  # the recurrence key — the underlying miss
    proposed_delta: str = ""           # the concrete proposed skill-change
    review: str = ""                   # "CONCUR by <r>" | "REFUTE by <r>; …" | ""
    next_time: str = ""                # the one thing to do differently
    concepts_touched: tuple[str, ...] = ()  # concept slugs whose STATE this updates


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
    # learning-loop v2: journal entries written + concept STATE files restamped +
    # the SURFACED graduation PROPOSALS (recurrence + CONCUR). The last is a
    # proposal list ONLY — nothing here is auto-applied to any skill file.
    journal_entries_written: dict[str, int] = field(default_factory=dict)
    concepts_restamped: list[str] = field(default_factory=list)
    graduations_proposed: list[str] = field(default_factory=list)  # "agent:symptom"

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
      3b. **Journal** -> append a ``JournalEntry`` (the merged lesson -> delta ->
         review -> next-time -> concept chain) to ``<brain>/wiki/<agent>/journal.md``
         (append-only; oldest rolls to ``journal-archive/`` when the hot file is
         large). A ``symptom`` seen a 2nd time (hot ∪ archive) WITH a CONCUR review
         flips ``graduation`` to ``ready`` — a SURFACED PROPOSAL only (never written
         to any skill file; the operator applies it later via skill_deltas).
      3c. **Concept restamp** -> for each touched concept, append a dated
         ``history:`` line to its STATE file + refresh ``state_updated`` (NEVER
         overwriting a prior history line).
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

            # 3b. journal growth — the merged lesson chain, with a SURFACED (never
            # auto-applied) graduation proposal on a 2nd-time symptom + CONCUR.
            graduated = _append_journal_entry(agent, pulse, date, brain_root)
            result.journal_entries_written[_slug(agent)] = (
                result.journal_entries_written.get(_slug(agent), 0) + 1
            )
            if graduated and pulse.symptom.strip():
                result.graduations_proposed.append(
                    f"{_slug(agent)}:{pulse.symptom.strip()}"
                )

            # 3c. concept restamp — append a dated history line to each touched
            # concept's STATE file (never overwriting a prior history line).
            for concept in pulse.concepts_touched:
                if _restamp_concept(agent, concept, pulse, date, brain_root):
                    result.concepts_restamped.append(
                        f"{_slug(agent)}/{_slug(concept)}"
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


# --------------------------------------------------------------------------- #
# 3b. Per-agent JOURNAL — the merged lesson chain + the graduation PROPOSAL    #
# --------------------------------------------------------------------------- #

_JOURNAL_HEADER = "# {slug} — journal\n\n"
_JOURNAL_INTRO = (
    "Append-only learning journal. One entry per folded pulse; newest at bottom. "
    "When the hot file is large the oldest entry rolls to `journal-archive/` "
    "(archive — never deleted; grep-reachable). A symptom that recurs AND a "
    "reviewer CONCURs flips `graduation` to `ready`: a SURFACED PROPOSAL the "
    "operator applies — it is NEVER auto-written into any skill.\n\n"
)
# A journal-entry block opens with the canonical header the schema renders.
_J_BLOCK_RE = re.compile(r"(?m)^### J-[^\n]+")


def _split_entry_blocks(text: str) -> list[str]:
    """Split a journal markdown file into its individual entry blocks (each begins
    with a ``### J-…`` header). Anything before the first header (the file header /
    intro) is dropped — it is regenerated, not an entry."""
    if not text:
        return []
    starts = [m.start() for m in _J_BLOCK_RE.finditer(text)]
    blocks = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(text)
        block = text[s:e].strip()
        if block:
            blocks.append(block)
    return blocks


def _parse_entries(text: str) -> list[JournalEntry]:
    """Parse every entry block in a journal file into JournalEntry objects.

    A block that fails the schema is SKIPPED here (a corrupt historical entry must
    not crash a fold) — but the schema parser still raises on a malformed *single*
    entry when called directly, so the never-silent contract holds at the unit
    boundary. Recurrence/numbering only need the entries that DO parse."""
    out = []
    for block in _split_entry_blocks(text):
        try:
            out.append(parse_journal_entry(block))
        except Exception:  # noqa: BLE001 — a corrupt past entry is skipped, not fatal
            continue
    return out


def _read_journal_history(agent: str, brain_root) -> tuple[list[JournalEntry], list[JournalEntry]]:
    """Return ``(hot_entries, all_entries)`` — the hot journal's parsed entries and
    the union of hot ∪ every archive file (the recurrence/numbering search space)."""
    hot_path = agent_journal_path(agent, brain_root)
    hot_text = ""
    if os.path.isfile(hot_path):
        with open(hot_path, "r", encoding="utf-8") as fh:
            hot_text = fh.read()
    hot_entries = _parse_entries(hot_text)

    all_entries = list(hot_entries)
    arch_dir = agent_journal_archive_dir(agent, brain_root)
    if os.path.isdir(arch_dir):
        for name in sorted(os.listdir(arch_dir)):
            if not name.endswith(".md"):
                continue
            with open(os.path.join(arch_dir, name), "r", encoding="utf-8") as fh:
                all_entries.extend(_parse_entries(fh.read()))
    return hot_entries, all_entries


def _symptom_seen_before(symptom: str, prior: Iterable[JournalEntry]) -> bool:
    """True iff ``symptom`` already appears in a prior entry (so the occurrence we
    are about to write is its 2nd). Normalized compare (case/whitespace), and the
    'n/a' / empty sentinel never counts (an absent symptom can't recur)."""
    norm = _norm(symptom)
    if not norm or norm == "n/a":
        return False
    return any(_norm(e.symptom) == norm for e in prior)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _review_concurs(review: str) -> bool:
    """True iff a recorded review is a CONCUR verdict (the only verdict that can
    graduate a recurrence). REFUTE / n/a / empty never graduate."""
    return _CONCUR.lower() in _norm(review).split()[:1] if review.strip() else False


def _append_journal_entry(agent: str, pulse: Pulse, date: str, brain_root) -> bool:
    """Append a JournalEntry for this pulse to the agent's hot journal (creating it
    with a header on first write), rolling the oldest entry to the archive when the
    hot file exceeds the soft cap. Returns ``True`` iff this entry GRADUATED — a
    SURFACED PROPOSAL (recurrence + CONCUR), which is NEVER written to any skill.

    Numbering (``J-<agent>-<NNN>``) continues across hot ∪ archive so an id is
    never reused after a roll."""
    path = agent_journal_path(agent, brain_root)
    _assert_under_wiki_root(path, brain_root)

    hot_entries, all_entries = _read_journal_history(agent, brain_root)

    # --- recurrence -> graduation (PROPOSAL ONLY; no skill is ever written) ---
    recurs = _symptom_seen_before(pulse.symptom, all_entries)
    graduated = bool(recurs and _review_concurs(pulse.review))
    if graduated:
        graduation = GRADUATION_READY
    elif recurs:
        # surfaced but not yet concurred — record WHY it didn't graduate (honest).
        graduation = f"recurrence-{_GRADUATION_RECURRENCE}x · awaiting CONCUR"
    else:
        graduation = "none"

    n = (max((e.n for e in all_entries), default=0)) + 1
    entry = JournalEntry(
        agent=_slug(agent),
        n=n,
        date=date,
        title=(pulse.title.strip() or pulse.lesson.strip() or "untitled"),
        worked_on=list(pulse.worked_on),
        understood=list(pulse.understood),
        lesson=pulse.lesson.strip() or "none",
        symptom=pulse.symptom.strip() or "n/a",
        proposed_delta=pulse.proposed_delta.strip() or "none",
        review=pulse.review.strip() or "n/a",
        next_time=pulse.next_time.strip(),
        concept_touched=[_slug(c) for c in pulse.concepts_touched],
        graduation=graduation,
    )

    os.makedirs(os.path.dirname(path), exist_ok=True)
    new_file = not os.path.isfile(path)
    with open(path, "a", encoding="utf-8") as fh:
        if new_file:
            fh.write(_JOURNAL_HEADER.format(slug=_slug(agent)) + _JOURNAL_INTRO)
        fh.write(render_journal_entry(entry) + "\n")

    # --- roll the oldest entry to the archive when the hot file is large ---
    # (hot_entries was the count BEFORE this append; +1 for the entry just added.)
    if len(hot_entries) + 1 > _HOT_JOURNAL_MAX:
        _roll_oldest_to_archive(agent, brain_root)

    return graduated


def _roll_oldest_to_archive(agent: str, brain_root) -> None:
    """Move the OLDEST entry out of the hot journal into a dated archive file
    (archive-don't-delete; the entry stays grep-reachable and still counts toward
    recurrence). Idempotent in shape: the hot file is rewritten header + remaining
    entries; the archive file is appended to."""
    hot_path = agent_journal_path(agent, brain_root)
    _assert_under_wiki_root(hot_path, brain_root)
    if not os.path.isfile(hot_path):
        return
    with open(hot_path, "r", encoding="utf-8") as fh:
        text = fh.read()
    blocks = _split_entry_blocks(text)
    if len(blocks) <= 1:
        return
    oldest, remaining = blocks[0], blocks[1:]

    arch_dir = agent_journal_archive_dir(agent, brain_root)
    _assert_under_wiki_root(arch_dir, brain_root)
    os.makedirs(arch_dir, exist_ok=True)
    # Period file = the rolled entry's own date (group by month so the archive is
    # navigable). Fall back to "undated" if the header has no parseable date.
    period = _entry_period(oldest)
    arch_path = os.path.join(arch_dir, f"{period}.md")
    _assert_under_wiki_root(arch_path, brain_root)
    arch_new = not os.path.isfile(arch_path)
    with open(arch_path, "a", encoding="utf-8") as fh:
        if arch_new:
            fh.write(f"# {_slug(agent)} — journal archive ({period})\n\n")
        fh.write(oldest.rstrip() + "\n\n")

    # rewrite the hot file = header + the remaining (newer) entries.
    with open(hot_path, "w", encoding="utf-8") as fh:
        fh.write(_JOURNAL_HEADER.format(slug=_slug(agent)) + _JOURNAL_INTRO)
        for b in remaining:
            fh.write(b.rstrip() + "\n\n")


def _entry_period(block: str) -> str:
    """Group key for an archived entry: ``YYYY-MM`` from the entry's date, else
    ``undated``."""
    try:
        date = parse_journal_entry(block).date
    except Exception:  # noqa: BLE001
        return "undated"
    m = re.match(r"(\d{4})-(\d{2})", date.strip())
    return f"{m.group(1)}-{m.group(2)}" if m else "undated"


# --------------------------------------------------------------------------- #
# 3c. Concept restamp — append a dated history line (NEVER overwrite)          #
# --------------------------------------------------------------------------- #


def _restamp_concept(agent: str, concept: str, pulse: Pulse, date: str, brain_root) -> bool:
    """Restamp a touched concept's STATE file: parse it, APPEND a dated ``history:``
    line, refresh ``state_updated``, re-render. Returns ``True`` iff a file existed
    and was restamped.

    Never overwrites or drops a prior history line — the whole point is that the
    concept's change-history grows monotonically (supersede-with-archive). A
    concept slug with no STATE file yet is a no-op here (the wiki builder owns
    creation; the fold only updates what exists)."""
    path = agent_concept_path(agent, concept, brain_root)
    _assert_under_wiki_root(path, brain_root)
    if not os.path.isfile(path):
        return False
    with open(path, "r", encoding="utf-8") as fh:
        cs = parse_concept_state(fh.read())

    # The new dated history line records WHAT touched the concept (the lesson) and
    # its source pulse — append-only; prior history is carried forward untouched.
    note = pulse.lesson.strip() or pulse.symptom.strip() or "touched by a fold"
    cs.history.append(f"{date} {note} (src {pulse.pulse_id})")
    cs.state_updated = date

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_concept_state(cs))
    return True


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
    "Pulse", "FoldResult", "POINTER", "FOLD", "GRADUATION_READY",
    "wiki_root", "agent_log_path",
    "agent_journal_path", "agent_journal_archive_dir", "agent_concept_path",
    "is_folded", "restamp_folded",
    "fold", "corpus_from_events",
]
