"""journal_schema — the canonical formats for the learning-loop v2 port.

Three shapes, ONE definition (so the wiki builder, the fold, status, and the
router can never drift apart). De-welded from the brain's
``agent-journal-learning-loop-v2.md`` §2.3 (journal entry), §3.1 (router index),
§3.2 (per-concept STATE file). Pure: render + parse, stdlib only, no I/O.

Round-trip is the contract: ``parse_X(render_X(x)) == x`` for every shape. A
malformed block raises ``SchemaError`` — never a silent partial parse (the
never-silent rule the whole loop exists to enforce).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List


class SchemaError(ValueError):
    """A block did not match its canonical shape (never parsed silently-wrong)."""


# --------------------------------------------------------------------------- #
# Concept STATE file (brain §3.2)                                              #
# --------------------------------------------------------------------------- #

_RECURRENT = "## RECURRENT STATE"
_OVERVIEW = "## HIGH-LEVEL OVERVIEW"
_SOURCES = "## SOURCE DOCS — read THESE for a quality answer"
_HISTORY_PREFIX = "history:"


@dataclass
class ConceptState:
    """One concept's knowledge state — what the agent reads FIRST after routing."""

    slug: str
    agent: str
    state_updated: str
    recurrent_state: List[str] = field(default_factory=list)  # claim · confidence · anchor
    history: List[str] = field(default_factory=list)          # dated change lines
    overview: str = ""                                        # the agent's synthesis
    source_docs: List[str] = field(default_factory=list)      # the read-THESE directive


def render_concept_state(cs: ConceptState) -> str:
    lines = [
        "---",
        f"concept: {cs.slug}",
        f"agent: {cs.agent}",
        f"state_updated: {cs.state_updated}",
        "---",
        "",
        f"# {cs.slug}",
        "",
        _RECURRENT + "  (what is true NOW — restamped each fold)",
        "",
    ]
    for claim in cs.recurrent_state:
        lines.append(f"- {claim}")
    for h in cs.history:
        lines.append(f"- {_HISTORY_PREFIX} {h}")
    if not cs.recurrent_state and not cs.history:
        lines.append("- (no state yet)")
    lines += ["", _OVERVIEW + "  (orient fast)", "", cs.overview or "(no overview yet)", ""]
    lines += [_SOURCES + "  (the routing payoff — read these primaries)", ""]
    for sd in cs.source_docs:
        lines.append(f"- {sd}")
    if not cs.source_docs:
        lines.append("- (no source docs pinned yet)")
    return "\n".join(lines) + "\n"


def parse_concept_state(md: str) -> ConceptState:
    fm = _frontmatter(md)
    for key in ("concept", "agent", "state_updated"):
        if key not in fm:
            raise SchemaError(f"concept state missing frontmatter key: {key!r}")
    body = md.split("---", 2)[-1]
    rec_block = _section(body, _RECURRENT)
    over_block = _section(body, _OVERVIEW)
    src_block = _section(body, _SOURCES)

    recurrent, history = [], []
    for b in _bullets(rec_block):
        if b.startswith(_HISTORY_PREFIX):
            history.append(b[len(_HISTORY_PREFIX):].strip())
        elif b != "(no state yet)":
            recurrent.append(b)
    overview = over_block.strip()
    if overview == "(no overview yet)":
        overview = ""
    source_docs = [b for b in _bullets(src_block) if b != "(no source docs pinned yet)"]
    return ConceptState(
        slug=fm["concept"], agent=fm["agent"], state_updated=fm["state_updated"],
        recurrent_state=recurrent, history=history, overview=overview,
        source_docs=source_docs,
    )


# --------------------------------------------------------------------------- #
# Journal entry (brain §2.3) — the merged lesson -> delta -> review chain      #
# --------------------------------------------------------------------------- #

_LIST_SEP = " ; "
# field key -> (is_list). Order is the canonical render order (§2.3).
_JOURNAL_FIELDS = [
    ("worked-on", True), ("understood", True), ("LESSON", False),
    ("symptom", False), ("PROPOSED-DELTA", False), ("REVIEW", False),
    ("next-time", False), ("concept-touched", True), ("graduation", False),
]
_J_HEADER = re.compile(r"^###\s+J-(?P<agent>[^\s·]+)-(?P<n>\d+)\s+·\s+(?P<date>[^·]+?)\s+·\s+(?P<title>.+)$")


@dataclass
class JournalEntry:
    agent: str
    n: int
    date: str
    title: str
    worked_on: List[str] = field(default_factory=list)
    understood: List[str] = field(default_factory=list)
    lesson: str = "none"
    symptom: str = "n/a"
    proposed_delta: str = "none"
    review: str = "n/a"          # "CONCUR by <r>" | "REFUTE by <r>; residuals: …" | "n/a"
    next_time: str = ""
    concept_touched: List[str] = field(default_factory=list)
    graduation: str = "none"     # none | ready | recurrence-2x → … | operator-flag


def render_journal_entry(je: JournalEntry) -> str:
    vals = {
        "worked-on": _LIST_SEP.join(je.worked_on) or "—",
        "understood": _LIST_SEP.join(je.understood) or "—",
        "LESSON": je.lesson or "none",
        "symptom": je.symptom or "n/a",
        "PROPOSED-DELTA": je.proposed_delta or "none",
        "REVIEW": je.review or "n/a",
        "next-time": je.next_time or "—",
        "concept-touched": _LIST_SEP.join(je.concept_touched) or "—",
        "graduation": je.graduation or "none",
    }
    lines = [f"### J-{je.agent}-{je.n:03d} · {je.date} · {je.title}"]
    for key, _is_list in _JOURNAL_FIELDS:
        lines.append(f"- {key}: {vals[key]}")
    return "\n".join(lines) + "\n"


def parse_journal_entry(md: str) -> JournalEntry:
    lines = [ln for ln in md.strip().splitlines() if ln.strip()]
    if not lines:
        raise SchemaError("empty journal entry")
    m = _J_HEADER.match(lines[0].strip())
    if not m:
        raise SchemaError(f"journal header not in canonical shape: {lines[0]!r}")
    got = {}
    for ln in lines[1:]:
        mm = re.match(r"^-\s+([^:]+):\s?(.*)$", ln.strip())
        if mm:
            got[mm.group(1).strip()] = mm.group(2).strip()

    def _list(key: str) -> List[str]:
        raw = got.get(key, "").strip()
        if raw in ("", "—"):
            return []
        return [p.strip() for p in raw.split(_LIST_SEP.strip()) if p.strip()]

    def _val(key: str, default: str) -> str:
        raw = got.get(key, default)
        return default if raw == "—" else raw

    return JournalEntry(
        agent=m.group("agent"), n=int(m.group("n")),
        date=m.group("date").strip(), title=m.group("title").strip(),
        worked_on=_list("worked-on"), understood=_list("understood"),
        lesson=_val("LESSON", "none"), symptom=_val("symptom", "n/a"),
        proposed_delta=_val("PROPOSED-DELTA", "none"), review=_val("REVIEW", "n/a"),
        next_time=_val("next-time", ""), concept_touched=_list("concept-touched"),
        graduation=_val("graduation", "none"),
    )


# --------------------------------------------------------------------------- #
# Router index (brain §3.1) — the thin concept router that replaces flat list  #
# --------------------------------------------------------------------------- #

@dataclass
class RouterRow:
    concept: str
    state: str                  # 1-line current truth + confidence
    overview_link: str          # concepts/<slug>.md §overview
    source_docs: str            # comma list of where to read


def render_router_index(agent: str, rows: List[RouterRow]) -> str:
    lines = [
        f"# {agent}'s Concept Index — route here first",
        "",
        "Match the task to a concept, open its state file, read the pointed-to source docs.",
        "",
        "| Concept | State (current truth + confidence) | Overview | Source docs to read |",
        "|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r.concept} | {r.state} | {r.overview_link} | {r.source_docs} |")
    if not rows:
        lines.append("| _(none — no concepts derived yet)_ | | | |")
    return "\n".join(lines) + "\n"


def parse_router_index(md: str) -> List[RouterRow]:
    rows: List[RouterRow] = []
    for ln in md.splitlines():
        ln = ln.strip()
        if not ln.startswith("|") or ln.startswith("|---") or "Concept |" in ln:
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) != 4 or cells[0].startswith("_(none"):
            continue
        rows.append(RouterRow(concept=cells[0], state=cells[1],
                              overview_link=cells[2], source_docs=cells[3]))
    return rows


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

def _frontmatter(md: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", md, re.DOTALL)
    if not m:
        raise SchemaError("missing YAML frontmatter block")
    out = {}
    for ln in m.group(1).splitlines():
        if ":" in ln:
            k, _, v = ln.partition(":")
            out[k.strip()] = v.strip()
    return out


def _section(body: str, header: str) -> str:
    """Text under a ``## header...`` up to the next ``## `` (header may carry a
    trailing parenthetical, so match on the stable prefix)."""
    lines = body.splitlines()
    out, capturing = [], False
    for ln in lines:
        if ln.startswith("## "):
            capturing = ln.startswith(header)
            continue
        if capturing:
            out.append(ln)
    return "\n".join(out).strip()


def _bullets(block: str) -> List[str]:
    return [ln.strip()[2:].strip() for ln in block.splitlines() if ln.strip().startswith("- ")]


__all__ = [
    "SchemaError",
    "ConceptState", "render_concept_state", "parse_concept_state",
    "JournalEntry", "render_journal_entry", "parse_journal_entry",
    "RouterRow", "render_router_index", "parse_router_index",
]
