"""train-on-docs — research/ingest docs -> vetted, cited, DRAFT agent-wiki pages.

THE GAP THIS CLOSES (plain terms): genesis reads the company into existence ONCE
(``run_genesis``, ``since="inception"``), and session pulses reach the wikis via
``loop/fold.py`` — but a NEW research doc that arrives AFTER genesis has no path
into an existing agent's wiki. This package is that path: it decides whether a
doc is even about the company, routes it to the owning specialist(s) (or HOLDS
it), drops what the wiki already cites, and proposes a cited DRAFT page for the
rest. Nothing is applied, sent, or promoted — every write lands ``proposed``.

THE CURRENCY is ``(Anchor, corpus)`` — the proven ``loop/fold.py`` pattern, NOT
"Claims". ``genesis/agent_wiki_builder.build_agent_wiki(owner, anchors, corpus,
...)`` takes Anchors + a corpus and re-derives the facts itself; there is no
public Events->Claims door, so the pipeline speaks Anchors end to end and reuses
the genesis builder for the actual page write (its anchor-or-drop + egress +
confined-write rails come along for free).

THE PIPELINE (the order) for one source doc (one ``source_id``):

    events (one source_id)
      -> [1] relevance-gate  (keep/drop + candidate owners, on event text)
      -> [2] router          (finalize owner[s] or HOLD, on doc text)
      -> [3] dedup-vs-wiki   (drop anchors the owner's wiki already cites/states)
      -> [4] build_agent_wiki(owner, novel_anchors, corpus)
      -> proposed DRAFT pages (carrying the genesis builder's derived tier)

THREE engines, ONE shared overlap primitive, ONE handoff contract:
  * ``_overlap.keyword_overlap`` — the single membership tally both relevance and
    router call (relevance normalizes by bank size; router uses raw hits).
  * ``relevance.RelevanceVerdict`` — produced by relevance, consumed by router
    (the router does NOT recompute relevance).
  * ``relevance.py`` / ``router.py`` / ``dedup.py`` — the three engines.
  * ``pipeline.py`` — the orchestrator + the two tiny adapters.
  * ``feedback.py`` — 2.5: the post-genesis "new ingest Event -> existing wiki".

Discipline (CODE, not aspiration):
  * **Proposals-only.** Every wiki write goes through ``build_agent_wiki`` and
    lands ``proposed``/DRAFT; nothing auto-applies, sends, or promotes.
  * **Off-topic -> NO write.** A doc that isn't about the company is dropped.
  * **Unroutable -> HELD.** A doc with no domain owner is held
    (``status="unassigned"``), never mis-filed under a catch-all.
  * **Keep-if-in-doubt.** Dedup only drops what the wiki already cites/states; a
    contradiction or update is KEPT (the resolver + review surface adjudicate),
    never silently dropped.

De-welded: no company / person names, no home paths. This package writes no
files of its own — every page is written by ``build_agent_wiki`` under its own
confinement root; the brain-rooted persistence is the caller's (e.g. ``fold.py``)
concern. Stdlib only; no network. ALL model judgment routes through the genesis
``LLM`` / ``EgressGate`` injected into ``build_agent_wiki``.
"""

from __future__ import annotations

# The genesis modules (agent_wiki_builder, genesis_pipeline, genesis_contracts)
# use FLAT imports and live one level up under ``genesis/``. Mirror the proven
# ``ingest/normalize.py`` bootstrap so flat ``from genesis_contracts import ...``
# resolves however these modules are imported (pytest from repo root, or direct).
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_GENESIS = os.path.join(_REPO_ROOT, "genesis")
for _p in (_REPO_ROOT, _GENESIS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ingest.train_on_docs._overlap import keyword_overlap  # noqa: E402
from ingest.train_on_docs.relevance import (  # noqa: E402
    AgentDomain,
    AgentScore,
    RelevanceVerdict,
    gate_document,
)
from ingest.train_on_docs.router import RouteDecision, RoutableDoc, route_doc  # noqa: E402
from ingest.train_on_docs.dedup import DedupResult, dedup_anchors_vs_wiki  # noqa: E402
from ingest.train_on_docs.pipeline import (  # noqa: E402
    TrainOnDocsResult,
    default_agent_domains,
    doc_anchors,
    train_on_doc,
    verdict_to_routable,
)
from ingest.train_on_docs.feedback import (  # noqa: E402
    FeedbackResult,
    feed_event_to_wiki,
)

__all__ = [
    "keyword_overlap",
    "AgentDomain",
    "AgentScore",
    "RelevanceVerdict",
    "gate_document",
    "RouteDecision",
    "RoutableDoc",
    "route_doc",
    "DedupResult",
    "dedup_anchors_vs_wiki",
    "TrainOnDocsResult",
    "default_agent_domains",
    "doc_anchors",
    "train_on_doc",
    "verdict_to_routable",
    "FeedbackResult",
    "feed_event_to_wiki",
]
