"""Agent-wiki builder (LANE L3 — un-defer train-on-docs).

The roster proposer (``roster_proposer.py``) emits an ``agent`` Proposal carrying
``suggested_wiki_sources`` — but nothing has, until now, BUILT the wiki. A named
agent with no brain is not "stood up." This module is the missing builder.

Per the agent-brain-wiki schema (the ``agent-brain-wiki`` doctrine + the
``train-on-docs`` skill), a new agent's wiki is:

    wiki/<agent>/
      index.md                      # catalog: source pages + concept pages, w/ a status col
      sources/<NN>_<slug>.md        # ONE cited page per source doc, carrying its anchors
      concepts/<topic>.md           # synthesis across the sources (cited)
      log.md                        # append-only ingest log

The agent is NOT stood up until its CITED wiki exists. This builds a **DRAFT**
wiki for a proposed *or* ratified agent.

Discipline enforced as code (the rails that make it safe):
  * **Proposals-only / DRAFT.** Every page is stamped ``status: 🟡 DRAFT``. This
    function BUILDS a draft wiki; it never ratifies, sends, or applies anything.
    The pipeline does not call it for an unratified proposal — only the
    review/ratify path does, via ``build_wiki_for_ratified_proposal``.
  * **Cite >=1 anchor per page (verify-before-relay).** A source page with no
    resolvable anchor is DROPPED; a concept page must cite >=1 anchor or it's
    dropped. ``index.md`` + ``log.md`` always carry the agent's own anchors.
  * **Egress.** Any foreign-model prompt is routed through ``egress.guard`` by
    the CALLER (here) before it reaches the model.
  * **Confined writes.** Every write resolves under ``genesis/out/wiki/`` or it
    raises (``_assert_under_wiki_root``); nothing escapes ``genesis/out/``.

Pure control flow; ALL model judgment goes through the injected ``llm`` (a
stub in tests). If the model returns nothing usable, pages fall back to
deterministic, corpus-grounded text — the corpus text IS the ground, so the
page is still cited.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Iterable

from genesis_contracts import (
    Anchor,
    Corpus,
    EgressGate,
    Event,
    LLM,
    Proposal,
)

# The wiki lives under the genesis OUT_DIR (genesis/out/wiki/). Importing the
# pipeline's OUT_DIR keeps the single confinement root in one place.
from genesis_pipeline import OUT_DIR

# loop/ holds journal_schema (the canonical learning-loop-v2 shapes). genesis/ is
# already on sys.path (this module imports flat); add loop/ the same way fold.py
# reaches into genesis/, so the concept STATE + router formats have ONE definition.
_LOOP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "loop")
if _LOOP not in sys.path:
    sys.path.insert(0, _LOOP)

from journal_schema import (  # noqa: E402
    ConceptState,
    RouterRow,
    render_concept_state,
    render_router_index,
)

WIKI_ROOT = os.path.join(OUT_DIR, "wiki")

# Page status stamp — the index/log container is DRAFT until the operator ratifies.
DRAFT = "🟡 DRAFT"

# Three honest draft-tiers (de-draft doctrine): reserve the confident label for
# what is actually confident, instead of stamping every fact-page the same.
# Derived PURELY from the claim-contract fields the page is built on — there is
# NO source-verification engine in MCS, so a machine-built page is never VERIFIED
# until its claim shows current + aligned (or the operator promotes it).
TIER_VERIFIED = "🟢 VERIFIED"
TIER_UNVERIFIED = "🟡 UNVERIFIED"
TIER_ASPIRATIONAL = "⚪ ASPIRATIONAL"


def derive_tier(
    *,
    has_anchor: bool,
    recency_status: str = "unknown",
    conflict_status: str = "unknown",
    confidence: str = "medium",
) -> str:
    """Map a claim's contract fields to one of three honest tiers (de-draft).

      * aspirational — no citation / forward-looking synthesis
      * verified     — cited AND recency 'current' AND conflict 'aligned' AND not low-confidence
      * unverified   — cited but stale/unknown recency, disputed, or low-confidence
    """
    if not has_anchor:
        return TIER_ASPIRATIONAL
    if recency_status == "current" and conflict_status == "aligned" and confidence != "low":
        return TIER_VERIFIED
    return TIER_UNVERIFIED

# A concept page is synthesized PER pillar/domain the agent owns; this slice
# emits one domain-level concept page (the agent's charter synthesis). Doctrine
# allows >=1; one cited concept page satisfies "concept page(s)".
_CONCEPT_TOPIC = "domain-overview"


# --------------------------------------------------------------------------- #
# LLM contracts (deterministic stub in tests)                                  #
# --------------------------------------------------------------------------- #

_SOURCE_SYSTEM = (
    "You distill ONE source document into a tight, factual wiki page for a "
    "specialist agent's brain. Return JSON: "
    '{"what_it_is": str, "known": [str, ...]}. '
    "Every 'known' item is a load-bearing fact stated plainly. No prose outside "
    "the JSON. Do not invent facts not present in the provided excerpts."
)

_CONCEPT_SYSTEM = (
    "You synthesize a specialist agent's domain across several source documents "
    "into ONE concept page. Return JSON: "
    '{"summary": str, "themes": [str, ...]}. '
    "The summary is 1-3 plain sentences; themes are the recurring threads. No "
    "prose outside the JSON. Do not invent claims beyond the provided excerpts."
)


# --------------------------------------------------------------------------- #
# Result object                                                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WikiBuildResult:
    """What ``build_agent_wiki`` produced — paths written + what was dropped.

    Asserts nothing as ratified; the wiki it points at is DRAFT."""

    agent_slug: str
    wiki_dir: str
    index_path: str
    log_path: str
    source_pages: list[str] = field(default_factory=list)
    concept_pages: list[str] = field(default_factory=list)
    dropped_sources: list[str] = field(default_factory=list)  # source_ids w/ no anchor

    @property
    def page_count(self) -> int:
        # index + log + sources + concepts
        return 2 + len(self.source_pages) + len(self.concept_pages)


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


def _slugify(text: str) -> str:
    """Lower-case, collapse non-alphanumerics to hyphens (stable file slugs)."""
    return re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-") or "x"


def _assert_under_wiki_root(path: str) -> None:
    """Guard: refuse any write whose resolved path escapes WIKI_ROOT.

    Mirrors ``genesis_pipeline._assert_under_out`` — defense in depth so a bad
    agent_slug (``../`` etc.) can never write outside ``genesis/out/wiki/``."""
    real_root = os.path.realpath(WIKI_ROOT)
    real_path = os.path.realpath(path)
    if not (real_path == real_root or real_path.startswith(real_root + os.sep)):
        raise RuntimeError(f"Refusing write outside WIKI_ROOT: {path}")


def _write(path: str, body: str) -> str:
    """Confined write — asserts under WIKI_ROOT, makes parent dirs, writes."""
    _assert_under_wiki_root(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def _anchors_from_proposal(proposal: Proposal) -> list[Anchor]:
    """The agent's anchors = the proposal's own ``source_anchors`` (already
    resolved + de-duped by the roster proposer)."""
    return list(proposal.source_anchors)


def _group_anchors_by_source(anchors: Iterable[Anchor]) -> dict[str, list[Anchor]]:
    """One source DOC == one ``source_id``; its anchors = every anchor that cites
    it. Deterministic first-seen ordering of source_ids."""
    grouped: dict[str, list[Anchor]] = {}
    for a in anchors:
        grouped.setdefault(a.source_id, []).append(a)
    return grouped


def _events_for_source(corpus: Corpus, source_id: str) -> list[Event]:
    """Pull the corpus events that back a given source_id (the page's grounded
    facts), in deterministic order."""
    out = [e for e in corpus.events_since("inception") if e.source_id == source_id]
    out.sort(key=lambda e: (e.observed_at, e.event_id))
    return out


def _anchor_md(a: Anchor) -> str:
    """Render one anchor as a markdown reference (the page's citation)."""
    loc = f"#{a.locator}" if a.locator else ""
    return f"`{a.source_id}{loc}` ({a.kind})"


def _anchor_directive(a: Anchor) -> str:
    """An anchor rendered as a read-THESE directive line (the routing payoff —
    the source-docs entry that tells the agent which primary to open). ONE
    backtick group (the anchor) so the source it points at is unambiguous; the
    canonical page carrying it is ``sources/<NN>_<slug-of-source_id>.md``."""
    return f"read {_anchor_md(a)} → in sources/"


def _excerpt(text: str, limit: int = 200) -> str:
    snippet = " ".join(text.split())
    return snippet if len(snippet) <= limit else snippet[: limit - 1] + "…"


def _parse_json_obj(raw: str) -> dict:
    """Best-effort parse of a JSON object from the model; {} on anything else."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _llm_source_distill(
    llm: LLM, egress: EgressGate, *, source_id: str, events: list[Event]
) -> dict:
    """Ask the model to distill a source page. Routed through egress (the prompt
    is corpus excerpts — guarded before it can leave). Falls back to {} when the
    model returns nothing usable; the caller then uses corpus text directly."""
    if not events:
        return {}
    excerpts = "\n".join(f"- [{e.kind}:{e.locator}] {_excerpt(e.text)}" for e in events)
    user = f"SOURCE: {source_id}\nEXCERPTS:\n{excerpts}\n\nReturn the JSON."
    safe_user = egress.guard(user)  # data-boundary rail (caller-side)
    raw = llm.complete(_SOURCE_SYSTEM, safe_user, max_tokens=512)
    return _parse_json_obj(raw)


def _llm_concept_synth(
    llm: LLM,
    egress: EgressGate,
    *,
    domain: str,
    by_source: dict[str, list[Anchor]],
    corpus: Corpus,
) -> dict:
    """Ask the model to synthesize the concept page across sources. Egress-guarded."""
    lines: list[str] = []
    for sid in by_source:
        for e in _events_for_source(corpus, sid)[:2]:
            lines.append(f"- [{sid}:{e.locator}] {_excerpt(e.text, 160)}")
    if not lines:
        return {}
    user = f"DOMAIN: {domain}\nACROSS SOURCES:\n" + "\n".join(lines) + "\n\nReturn the JSON."
    safe_user = egress.guard(user)
    raw = llm.complete(_CONCEPT_SYSTEM, safe_user, max_tokens=512)
    return _parse_json_obj(raw)


# --------------------------------------------------------------------------- #
# Page renderers                                                               #
# --------------------------------------------------------------------------- #


def _render_source_page(
    *,
    number: int,
    slug: str,
    source_id: str,
    anchors: list[Anchor],
    events: list[Event],
    distilled: dict,
) -> str:
    """One CITED source page. Carries its anchors; binds every load-bearing fact
    to the source. Caller guarantees ``anchors`` is non-empty (else dropped)."""
    kinds = sorted({a.kind for a in anchors})
    what = str(distilled.get("what_it_is", "")).strip()
    if not what:
        what = (
            f"Source document `{source_id}` "
            f"({', '.join(kinds)}) cited in the agent's roster proposal."
        )

    known = [str(k).strip() for k in distilled.get("known", []) if str(k).strip()]
    if not known:
        # Deterministic fallback: the corpus text IS the ground.
        known = [_excerpt(e.text) for e in events] or [
            "(no excerpt text available; see source anchor)"
        ]

    # de-draft tier: a cited source page is UNVERIFIED (machine-distilled, not yet
    # confirmed); the builder has no recency/conflict status, so it never reaches
    # VERIFIED here (reserved for current+aligned claims / operator promotion).
    tier = derive_tier(has_anchor=bool(anchors))
    lines = [
        "---",
        f"source_doc: {source_id}",
        f"source_anchor: {source_id}",
        f"kinds: [{', '.join(kinds)}]",
        f"confirmation: {tier}",
        "confidence: medium",
        "canonical: true",
        f"status: {tier}",
        "---",
        "",
        f"# Source {number:02d} — {source_id}",
        "",
        "## What it is",
        "",
        what,
        "",
        "## What's known (load-bearing — each bound to the source)",
        "",
    ]
    for fact in known:
        lines.append(f"- {fact}")
    lines += [
        "",
        "## UNCONFIRMED / caveats",
        "",
        f"- DRAFT page, machine-distilled from the corpus; not yet operator-verified.",
        "",
        "## Source anchors (citations)",
        "",
    ]
    for a in anchors:
        lines.append(f"- {_anchor_md(a)}")
    lines += ["", "## Cross-links", "", "- index: ../index.md", ""]
    return "\n".join(lines) + "\n"


def _build_concept_state(
    *,
    slug: str,
    agent: str,
    domain: str,
    source_numbers: list[int],
    anchors: list[Anchor],
    synth: dict,
    state_updated: str,
) -> ConceptState:
    """Build the per-concept STATE file (the learning-loop-v2 shape) — what the
    agent reads FIRST after routing. The FORMAT upgrade of the old synthesis
    page; same grounded content, now in the 3-part recurrent-state / overview /
    source-docs shape. Caller guarantees >=1 anchor.

      * ``recurrent_state`` — the concept's claims (the synth summary + each
        theme), each tier-stamped (``derive_tier`` — UNVERIFIED for a machine-
        built cited concept) and anchored to a backing source. This is the
        "what is true NOW" the fold restamps.
      * ``overview`` — the existing synthesis body (orient fast).
      * ``source_docs`` — the concept's anchors rendered as a read-THESE
        directive (the routing payoff: open these primaries for a real answer).
    """
    summary = str(synth.get("summary", "")).strip()
    if not summary:
        summary = (
            f"Synthesis of the {domain or 'agent'} domain across "
            f"{len(source_numbers)} source document(s)."
        )
    themes = [str(t).strip() for t in synth.get("themes", []) if str(t).strip()]

    # The concept is cited but machine-built (no recency/conflict status) -> the
    # same UNVERIFIED tier the source pages carry. Reserved-confidence honoured.
    tier = derive_tier(has_anchor=bool(anchors))
    # First backing anchor as the claim's citation (deterministic, first-seen).
    cite = _anchor_md(anchors[0]) if anchors else ""

    recurrent_state: list[str] = [f"{summary} · {tier} · {cite}".rstrip(" ·")]
    for t in themes:
        recurrent_state.append(f"theme: {t} · {tier} · {cite}".rstrip(" ·"))

    source_docs = [_anchor_directive(a) for a in anchors]

    return ConceptState(
        slug=slug,
        agent=agent,
        state_updated=state_updated or "YYYY-MM-DD",
        recurrent_state=recurrent_state,
        history=[],
        overview=summary,
        source_docs=source_docs,
    )


def _concept_router_row(cs: ConceptState) -> RouterRow:
    """Derive the concept's router-index row from its STATE file — slug · 1-line
    recurrent state · overview link · the source-docs to read. Pipes are escaped
    so a claim can never break the markdown table."""
    top = cs.recurrent_state[0] if cs.recurrent_state else "(no state yet)"
    state_1line = _excerpt(top.replace("|", "/"), 120)
    src = ", ".join(sd.replace("|", "/") for sd in cs.source_docs) or "(none)"
    return RouterRow(
        concept=cs.slug,
        state=state_1line,
        overview_link=f"concepts/{cs.slug}.md §overview",
        source_docs=src,
    )


def _render_index(
    *,
    agent_slug: str,
    domain: str,
    proposal_status: str,
    router_rows: list[RouterRow],
) -> str:
    """The CONCEPT ROUTER ``index.md`` (learning-loop-v2 §3.1): match a task to a
    concept, open its STATE file, read the pointed-to source docs. The flat
    catalog is replaced by ``render_router_index`` (ONE shared definition) —
    wrapped with the DRAFT / ``proposal_status`` frontmatter the index has always
    carried (nothing here is ratified), and a pointer to the sources/log so the
    ledger links aren't lost."""
    table = render_router_index(agent_slug, router_rows)
    head = [
        "---",
        f"agent: {agent_slug}",
        f"domain: {domain}",
        f"proposal_status: {proposal_status}",
        f"status: {DRAFT}",
        "---",
        "",
    ]
    tail = [
        "",
        "---",
        "",
        "Per-concept STATE files live in `concepts/`; the canonical cited source "
        "pages they point to live in `sources/`; the append-only build/ingest "
        "ledger is `log.md`. Nothing here is ratified — DRAFT, proposals-only.",
        "",
    ]
    return "\n".join(head) + table + "\n".join(tail) + "\n"


def _render_log(
    *,
    agent_slug: str,
    domain: str,
    proposal_status: str,
    n_sources: int,
    n_concepts: int,
    dropped: list[str],
    anchors: list[Anchor],
    today: str,
) -> str:
    stamp = today or "YYYY-MM-DD"
    lines = [
        f"# {agent_slug} — wiki log",
        "",
        "Append-only. One entry per ingest/build operation; newest at bottom.",
        "",
        f"## [{stamp}] build | DRAFT wiki seeded from roster proposal",
        "",
        f"- agent: `{agent_slug}` · domain: {domain or 'unspecified'} · "
        f"proposal status at build: {proposal_status}",
        f"- source pages written: {n_sources}",
        f"- concept pages written: {n_concepts}",
        f"- anchors carried: {len(anchors)}",
    ]
    if dropped:
        lines.append(
            f"- DROPPED (no resolvable anchor, verify-before-relay): "
            + ", ".join(f"`{d}`" for d in dropped)
        )
    else:
        lines.append("- dropped sources: none")
    lines += [
        "- Fact pages stamped by tier (unverified source / aspirational synthesis); nothing ratified, sent, or applied.",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# The builder                                                                  #
# --------------------------------------------------------------------------- #


def build_agent_wiki(
    agent_slug: str,
    sources: Iterable[Anchor],
    corpus: Corpus,
    llm: LLM,
    egress: EgressGate,
    *,
    domain: str = "",
    proposal_status: str = "proposed",
    today: str = "",
) -> WikiBuildResult:
    """Build a DRAFT cited wiki for a proposed/ratified agent under
    ``genesis/out/wiki/<agent>/``.

    Writes: ``index.md`` + one cited ``sources/<NN>_<slug>.md`` per source doc
    (each carrying its anchors) + a synthesized ``concepts/<topic>.md`` + a
    ``log.md`` — per the agent-brain-wiki schema.

    Rails (CODE, not aspiration):
      * Every page is ``status: 🟡 DRAFT``; this builds, never ratifies/applies.
      * A source page with no resolvable anchor is DROPPED (cite >=1 per page).
      * The concept page is written ONLY if >=1 anchor survives.
      * Every foreign-model prompt is ``egress.guard``-ed before it leaves.
      * Every write is confined under ``genesis/out/wiki/`` (asserted).

    Args:
        agent_slug: the agent's slug (a directory-safe slug is derived from it).
        sources: the agent's anchors (the proposal's ``source_anchors``).
        corpus: the ingested corpus (grounds each page's facts).
        llm: injected model (all judgment routes through it; stub in tests).
        egress: data-boundary gate (guards every foreign-model prompt).
        domain: the agent's domain label (for the index/concept headers).
        proposal_status: the proposal's status at build time (recorded, not acted on).
        today: optional ISO date stamped onto the log entry.

    Returns:
        A ``WikiBuildResult`` with the paths written and any dropped source_ids.
    """
    slug = _slugify(agent_slug)
    wiki_dir = os.path.join(WIKI_ROOT, slug)
    _assert_under_wiki_root(wiki_dir)

    all_anchors = list(sources)
    by_source = _group_anchors_by_source(all_anchors)

    source_pages: list[str] = []
    source_numbers: list[int] = []
    dropped: list[str] = []
    kept_anchors: list[Anchor] = []

    number = 0
    for source_id in by_source:
        anchors = by_source[source_id]
        # cite >=1 anchor per page — a source with no resolvable anchor is dropped.
        resolvable = [a for a in anchors if a.source_id]
        if not resolvable:
            dropped.append(source_id)
            continue

        number += 1
        page_slug = _slugify(source_id)
        events = _events_for_source(corpus, source_id)
        distilled = _llm_source_distill(
            llm, egress, source_id=source_id, events=events
        )
        body = _render_source_page(
            number=number,
            slug=page_slug,
            source_id=source_id,
            anchors=resolvable,
            events=events,
            distilled=distilled,
        )
        rel = f"sources/{number:02d}_{page_slug}.md"
        path = _write(os.path.join(wiki_dir, rel), body)
        source_pages.append(path)
        source_numbers.append(number)
        kept_anchors.extend(resolvable)

    # Concept STATE file(s) — written ONLY if >=1 anchor survived (cite >=1 per
    # page). The format upgrade: a learning-loop-v2 ConceptState (recurrent-state
    # + overview + source-docs directive), and a router row derived from it.
    concept_pages: list[str] = []
    router_rows: list[RouterRow] = []
    if kept_anchors:
        synth = _llm_concept_synth(
            llm, egress, domain=domain, by_source=by_source, corpus=corpus
        )
        cs = _build_concept_state(
            slug=_CONCEPT_TOPIC,
            agent=slug,
            domain=domain,
            source_numbers=source_numbers,
            anchors=kept_anchors,
            synth=synth,
            state_updated=today,
        )
        rel = f"concepts/{_CONCEPT_TOPIC}.md"
        path = _write(os.path.join(wiki_dir, rel), render_concept_state(cs))
        concept_pages.append(path)
        router_rows.append(_concept_router_row(cs))

    # index.md is the concept ROUTER (route -> concept -> source docs); log.md is
    # the append-only ledger. Both always written (they ARE the agent's catalog).
    index_body = _render_index(
        agent_slug=slug,
        domain=domain,
        proposal_status=proposal_status,
        router_rows=router_rows,
    )
    index_path = _write(os.path.join(wiki_dir, "index.md"), index_body)

    # log.md carries the agent's own anchors (the kept ones, or — if every source
    # was uncitable — the full proposal set, so the ledger still shows them).
    log_anchors = kept_anchors or all_anchors
    log_body = _render_log(
        agent_slug=slug,
        domain=domain,
        proposal_status=proposal_status,
        n_sources=len(source_pages),
        n_concepts=len(concept_pages),
        dropped=dropped,
        anchors=log_anchors,
        today=today,
    )
    log_path = _write(os.path.join(wiki_dir, "log.md"), log_body)

    return WikiBuildResult(
        agent_slug=slug,
        wiki_dir=wiki_dir,
        index_path=index_path,
        log_path=log_path,
        source_pages=source_pages,
        concept_pages=concept_pages,
        dropped_sources=dropped,
    )


# --------------------------------------------------------------------------- #
# The ratify-path entry point (wires the builder into the flow)                #
# --------------------------------------------------------------------------- #


def build_wiki_for_ratified_proposal(
    proposal: Proposal,
    corpus: Corpus,
    llm: LLM,
    egress: EgressGate,
    *,
    today: str = "",
    require_ratified: bool = True,
) -> WikiBuildResult:
    """The function the review/ratify path calls when an ``agent`` roster
    proposal is RATIFIED — it stands the agent up by building its DRAFT wiki.

    This is the ONLY auto-trigger seam, and it does NOT auto-apply an unratified
    proposal: by default it refuses any proposal whose ``status`` is not
    ``"ratified"``. The genesis pipeline never calls this (it only emits
    ``status="proposed"`` packets); the operator's ratify step does.

    Set ``require_ratified=False`` ONLY to build a DRAFT *preview* wiki for a
    still-``proposed`` proposal (e.g. to show the operator what a wiki would look
    like before they ratify) — the wiki is still DRAFT and nothing is applied.

    Raises:
        ValueError: if ``proposal`` is not an ``agent`` proposal, is un-anchored,
            or (when ``require_ratified``) is not yet ``status="ratified"``.
    """
    if proposal.type != "agent":
        raise ValueError(
            f"build_wiki_for_ratified_proposal expects an 'agent' proposal; "
            f"got type={proposal.type!r}"
        )
    if not proposal.is_anchored():
        # verify-before-relay: an un-anchored proposal can't stand up a cited wiki.
        raise ValueError(
            f"proposal {proposal.id!r} has no source anchors; refusing to build "
            "a wiki with no citations."
        )
    if require_ratified and proposal.status != "ratified":
        raise ValueError(
            f"refusing to build a wiki for a non-ratified proposal "
            f"(status={proposal.status!r}); ratify it first, or pass "
            "require_ratified=False for a DRAFT preview."
        )

    slug = str(proposal.payload.get("slug", "")).strip() or proposal.id
    domain = str(proposal.payload.get("domain", "")).strip()
    return build_agent_wiki(
        slug,
        _anchors_from_proposal(proposal),
        corpus,
        llm,
        egress,
        domain=domain,
        proposal_status=proposal.status,
        today=today,
    )
