"""Engine 2 — dedup-vs-wiki.

THE QUESTION (plain terms): of the anchors this doc would cite, which does the
owning agent's wiki ALREADY carry? Re-citing the same source or re-stating the
same fact just bloats the page. This engine drops the already-known and keeps the
genuinely novel — conservatively (keep-if-in-doubt; never silently drop new
evidence).

Two tiers (cheap-first):
  * **Tier-1 exact** — the candidate ``Anchor`` already appears in the wiki's
    ``## Source anchors`` citation block (rendered by ``_anchor_md``). Certain
    duplicate.
  * **Tier-2 near** — the corpus text BEHIND the anchor (``_events_for_source``)
    closely restates an existing ``## What's known`` bullet: token-Jaccard OR
    ``difflib.ratio`` >= ``similarity_threshold`` (0.82). A paraphrase duplicate.
  * else **novel** — KEPT.

NOT dedup's job — supersede / conflict. A kept anchor that UPDATES or CONTRADICTS
a known fact is still KEPT; it flows to the wiki builder + the three-tier / review
surface + the resolver's existing supersede-with-archive. Dedup only removes what
is already cited / stated. (Karpathy: do the one job; don't smuggle adjudication
in here.)

Reuse:
  * ``agent_wiki_builder._anchor_md`` (the citation format -> Tier-1 membership),
  * ``agent_wiki_builder._events_for_source`` (the corpus text behind an anchor
    -> Tier-2),
  * ``ingest/dedupe.py:stable_digest`` (a stable, raw-text-free verdict key).

Divergence from the spec's reuse list: ``genesis_pipeline._parse_prior_fact_values``
parses the *pillar-draft* ``## Current claims`` block (``- [tier] key = value —
summary``), NOT the agent-wiki ``## What's known`` bullets (``- key = value``)
this engine reads — so it can't be reused as-is. The fact-value compare here uses
the SAME ``key = value`` shape as ``genesis_pipeline._FACT_RE`` (the contradiction
guard below), applied to the wiki's own bullet format.

Pure; stdlib only; no I/O. No company / person names.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from genesis_contracts import Anchor, Corpus, Event  # noqa: E402

import agent_wiki_builder as awb  # noqa: E402  (genesis/ on sys.path via package __init__)
from ingest.dedupe import stable_digest


# --------------------------------------------------------------------------- #
# Result + per-anchor verdict                                                  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AnchorVerdict:
    """Why one candidate anchor was kept or dropped.

      * ``anchor``  — the candidate;
      * ``novel``   — kept (not already cited/stated)?
      * ``tier``    — ``"exact"`` | ``"near"`` (when dropped) | ``"novel"``;
      * ``score``   — the Tier-2 similarity that triggered a near-dup (else 0.0);
      * ``key``     — a stable, raw-text-free digest of the verdict.
    """

    anchor: Anchor
    novel: bool
    tier: str
    score: float = 0.0
    key: str = ""


@dataclass(frozen=True)
class DedupResult:
    """What dedup decided for one doc's candidate anchors.

      * ``novel_anchors`` — the anchors to actually build a page from (kept);
      * ``duplicates``    — the anchors the wiki already cites/states (dropped);
      * ``verdicts``      — the per-anchor reasons (parallel to the inputs).
    """

    novel_anchors: list[Anchor] = field(default_factory=list)
    duplicates: list[Anchor] = field(default_factory=list)
    verdicts: list[AnchorVerdict] = field(default_factory=list)

    @property
    def all_novel(self) -> bool:
        return not self.duplicates


# --------------------------------------------------------------------------- #
# Wiki-text harvesting (what the wiki already knows)                            #
# --------------------------------------------------------------------------- #

_BULLET_RE = re.compile(r"^\s*-\s+(.*\S)\s*$")


def _known_bullets(wiki_pages: Iterable[str]) -> list[str]:
    """Every ``## What's known`` bullet across the wiki pages (the existing
    stated facts a near-dup is compared against). Heading-scoped so a citation
    bullet under ``## Source anchors`` is never mistaken for a fact."""
    out: list[str] = []
    for page in wiki_pages:
        in_known = False
        for raw in (page or "").splitlines():
            line = raw.strip()
            if line.startswith("## "):
                in_known = line.lower().startswith("## what's known")
                continue
            if not in_known:
                continue
            m = _BULLET_RE.match(raw)
            if m:
                out.append(m.group(1).strip())
    return out


def _all_text(wiki_pages: Iterable[str]) -> str:
    """The wiki pages concatenated (the Tier-1 citation-block haystack)."""
    return "\n".join(p or "" for p in wiki_pages)


# --------------------------------------------------------------------------- #
# Similarity (Tier-2)                                                          #
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _token_jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _similarity(a: str, b: str) -> float:
    """Max of token-Jaccard and ``difflib.ratio`` — Jaccard catches a reworded
    restatement (same words, shuffled); difflib catches a near-verbatim edit."""
    return max(_token_jaccard(a, b), difflib.SequenceMatcher(None, a, b).ratio())


def _anchor_corpus_text(corpus: Corpus, source_id: str) -> str:
    """The corpus text behind an anchor's source (its grounded facts), joined.

    Reuses ``agent_wiki_builder._events_for_source`` so the text the dedup
    compares is EXACTLY the text the builder would render into the page."""
    events: list[Event] = awb._events_for_source(corpus, source_id)
    return " ".join(e.text for e in events)


# --------------------------------------------------------------------------- #
# Fact-contradiction guard (the "never silently drop an UPDATE" rail)          #
# --------------------------------------------------------------------------- #
#
# Tier-2 textual similarity has a known blind spot: a contradicting UPDATE that
# only changes a tracked value (``price = 5200`` -> ``price = 4900``) is ~0.95
# similar and would be DROPPED as a near-dup — silently losing the correction.
# The spec forbids that (line 94: contradiction/update -> KEPT). So before a
# near-dup drop, we check whether the candidate asserts a tracked ``key = value``
# whose value DIFFERS from what the wiki states for that key; if so it is an
# update, not a duplicate, and is KEPT (the resolver + review adjudicate the
# supersede). Same ``key = value`` shape as ``genesis_pipeline._FACT_RE``.

_FACT_RE = re.compile(r"(?P<key>[a-z0-9_]+)\s*=\s*(?P<value>.+?)\s*$", re.IGNORECASE)


def _fact_values(text: str) -> dict[str, str]:
    """Extract ``{key: value}`` facts from ``key = value`` lines in ``text``
    (same shape ``genesis_pipeline._FACT_RE`` parses). Last value wins per key;
    keys/values normalized for a stable compare."""
    facts: dict[str, str] = {}
    for raw in (text or "").replace(";", "\n").splitlines():
        m = _FACT_RE.search(raw.strip())
        if m:
            facts[m.group("key").strip().lower()] = m.group("value").strip().lower()
    return facts


def _contradicts_known(cand_text: str, known_bullets: Iterable[str]) -> bool:
    """True iff the candidate asserts a tracked ``key = value`` whose value
    DIFFERS from the value the wiki states for that same key (an UPDATE, not a
    restatement). Equal values are NOT a contradiction (that's a true dup)."""
    cand_facts = _fact_values(cand_text)
    if not cand_facts:
        return False
    known_facts: dict[str, str] = {}
    for bullet in known_bullets:
        known_facts.update(_fact_values(bullet))
    for key, value in cand_facts.items():
        if key in known_facts and known_facts[key] != value:
            return True
    return False


# --------------------------------------------------------------------------- #
# The engine                                                                   #
# --------------------------------------------------------------------------- #


def dedup_anchors_vs_wiki(
    candidate_anchors: Sequence[Anchor],
    corpus: Corpus,
    wiki_pages: Sequence[str],
    *,
    similarity_threshold: float = 0.82,
) -> DedupResult:
    """Drop the candidate anchors the owner's wiki already cites or states.

    Procedure per anchor (cheap-first; keep-if-in-doubt):
      1. **Tier-1 exact** — the rendered citation (``_anchor_md``) is already in
         the wiki text -> duplicate.
      2. **Tier-2 near** — the corpus text behind the anchor's source restates an
         existing ``## What's known`` bullet at >= ``similarity_threshold`` ->
         duplicate.
      3. else **novel** -> KEPT.

    An empty wiki (no pages / no matching text) keeps everything. A contradiction
    or update is NOT special-cased here: if it isn't already cited/stated it is
    novel and KEPT (the resolver + review surface adjudicate supersede/conflict).

    Reuse: ``_anchor_md`` (citation format), ``_events_for_source`` (corpus text),
    ``stable_digest`` (raw-text-free verdict key).
    """
    wiki_text = _all_text(wiki_pages)
    known = _known_bullets(wiki_pages)

    novel: list[Anchor] = []
    dups: list[Anchor] = []
    verdicts: list[AnchorVerdict] = []

    for anchor in candidate_anchors:
        # Tier-1 exact: the rendered citation already present in the wiki text.
        citation = awb._anchor_md(anchor)
        if citation and citation in wiki_text:
            key = stable_digest(
                {"source_id": anchor.source_id, "locator": anchor.locator,
                 "kind": anchor.kind, "tier": "exact"}
            )
            dups.append(anchor)
            verdicts.append(AnchorVerdict(anchor, False, "exact", 1.0, key))
            continue

        # Tier-2 near: the corpus text behind the anchor restates a known bullet.
        cand_text = _anchor_corpus_text(corpus, anchor.source_id)
        best = 0.0
        if cand_text and known:
            best = max((_similarity(cand_text, b) for b in known), default=0.0)
        if best >= similarity_threshold:
            # Guard: a high-similarity text that CHANGES a tracked fact value is
            # an UPDATE, not a duplicate (e.g. ``price = 5200`` -> ``price =
            # 4900`` is ~0.95 similar). Never silently drop it — KEEP it; the
            # resolver + review surface adjudicate the supersede (spec line 94).
            if _contradicts_known(cand_text, known):
                key = stable_digest(
                    {"source_id": anchor.source_id, "locator": anchor.locator,
                     "kind": anchor.kind, "tier": "update"}
                )
                novel.append(anchor)
                verdicts.append(AnchorVerdict(anchor, True, "update", best, key))
                continue
            key = stable_digest(
                {"source_id": anchor.source_id, "locator": anchor.locator,
                 "kind": anchor.kind, "tier": "near"}
            )
            dups.append(anchor)
            verdicts.append(AnchorVerdict(anchor, False, "near", best, key))
            continue

        key = stable_digest(
            {"source_id": anchor.source_id, "locator": anchor.locator,
             "kind": anchor.kind, "tier": "novel"}
        )
        novel.append(anchor)
        verdicts.append(AnchorVerdict(anchor, True, "novel", best, key))

    return DedupResult(novel_anchors=novel, duplicates=dups, verdicts=verdicts)


__all__ = ["AnchorVerdict", "DedupResult", "dedup_anchors_vs_wiki"]
