"""Genesis intelligence — shared contracts + the enforced rails.

BUILD-SPEC-02 deliverable 5. Stdlib-only. Defines the dataclasses + protocols
the intelligence layer is built on, plus the two safety rails that are CODE,
not aspiration:

  * ``EgressGate`` — the data-boundary rail (SDL-23): nothing private leaves to
    a foreign model. ``guard()`` raises ``PrivateDataEgressError`` on private
    content; unclassifiable text is treated as private.
  * The proposal/anchor contracts that make "every proposal cites >=1 anchor or
    it's dropped" (SDL-19 verify-before-relay) expressible downstream.

Nothing here does network or file I/O. The ``LLM`` is a protocol so tests
inject a deterministic stub.
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Protocol, runtime_checkable

# The egress rail now lives in the repo-root reusable module (``mcs_egress``) so
# any skill — not just genesis — can guard with it. The genesis modules are run
# with ``genesis/`` on sys.path (flat imports), and ``mcs_egress`` is one level
# up at the repo root, so we put the repo root on sys.path here. This shim
# exists ONLY because the repo isn't yet packaged as an installable
# distribution; once it is, this can go.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# --------------------------------------------------------------------------- #
# Core value objects                                                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Anchor:
    """A citation into the corpus/pillar. Every proposal must carry >=1."""

    source_id: str
    kind: str        # e.g. "email" | "meeting" | "pillar" | "transcript"
    locator: str     # e.g. "msg7" | "L42" | "slide2"


@dataclass(frozen=True)
class Event:
    """One ingested corpus event (full-corpus mode walks these)."""

    event_id: str
    observed_at: str            # ISO-8601 UTC
    kind: str
    text: str
    source_id: str
    locator: str = ""
    participants: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    def anchor(self) -> Anchor:
        return Anchor(source_id=self.source_id, kind=self.kind, locator=self.locator)


@dataclass(frozen=True)
class Proposal:
    """An operator-gated suggestion. NEVER auto-applies; ``status`` stays
    ``"proposed"`` until a later, separate ratification step. A proposal with
    zero ``source_anchors`` is invalid (dropped by the pipeline)."""

    id: str
    type: str                                  # "meta_initiative" | "agent" | "doc_reorg"
    confidence: str                            # "high" | "medium" | "low"
    rationale: str
    source_anchors: tuple[Anchor, ...]
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "proposed"

    def is_anchored(self) -> bool:
        return len(self.source_anchors) >= 1


def new_proposal(
    *,
    type: str,
    confidence: str,
    rationale: str,
    source_anchors: Iterable[Anchor],
    payload: dict[str, Any] | None = None,
    id: str | None = None,
) -> Proposal:
    """Construct a ``status="proposed"`` proposal with a stable-ish id.

    The id is derived deterministically from (type + payload) when not given,
    so the same logical proposal gets the same id across runs (helps tests +
    dedupe). Falls back to a uuid5 over the rationale if payload is empty.
    """
    if id is None:
        basis = f"{type}|{sorted((payload or {}).items())}|{rationale}"
        id = f"{type}-{uuid.uuid5(uuid.NAMESPACE_URL, basis).hex[:12]}"
    return Proposal(
        id=id,
        type=type,
        confidence=confidence,
        rationale=rationale,
        source_anchors=tuple(source_anchors),
        payload=dict(payload or {}),
        status="proposed",
    )


@dataclass
class PillarState:
    """The populated state of one pillar after genesis ingest."""

    name: str
    summary: str = ""
    claims: list[Any] = field(default_factory=list)      # resolved Claims
    anchors: list[Anchor] = field(default_factory=list)
    draft_path: str | None = None                        # where the draft was written

    @property
    def anchor_count(self) -> int:
        return len(self.anchors)


@dataclass(frozen=True)
class ReviewPacket:
    """Type-2 (FOR-THE-OPERATOR) review surface. Asserts nothing as fact;
    applies nothing. ``summary_md`` opens 'In plain terms' and carries no raw
    source_id codes in the prose (anchors live in a separate evidence block)."""

    pillars: dict[str, PillarState]
    proposals: list[Proposal]
    summary_md: str


# --------------------------------------------------------------------------- #
# Protocols (injected; tests use deterministic stubs)                          #
# --------------------------------------------------------------------------- #


@runtime_checkable
class LLM(Protocol):
    """Model interface. The pipeline routes ALL model judgment through this so
    it is swappable + testable. Implementations that hit a foreign model must
    have their prompt passed through ``EgressGate.guard`` by the CALLER first."""

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        ...


@runtime_checkable
class Corpus(Protocol):
    """The ingested corpus. ``events_since`` drives full-corpus vs windowed."""

    def events_since(self, since: str) -> Iterator[Event]:
        ...


# --------------------------------------------------------------------------- #
# A concrete in-memory Corpus (handy for the pipeline demo + tests)            #
# --------------------------------------------------------------------------- #


class InMemoryCorpus:
    """A simple list-backed corpus. ``since="inception"`` -> no lower bound;
    any ISO date -> only strictly-newer events. Deterministic ordering."""

    def __init__(self, events: Iterable[Event]):
        # keep input order stable, but expose a sorted view for determinism
        self._events: list[Event] = list(events)

    def events_since(self, since: str) -> Iterator[Event]:
        ordered = sorted(self._events, key=lambda e: (e.observed_at, e.event_id))
        if since == "inception":
            yield from ordered
            return
        for e in ordered:
            if e.observed_at > since:
                yield e

    def all_events(self) -> list[Event]:
        return list(self._events)


# --------------------------------------------------------------------------- #
# The EgressGate rail (SDL-23 data-boundary)                                  #
# --------------------------------------------------------------------------- #

# The data-boundary egress rail now lives in the repo-root reusable module so
# any skill (not just genesis) can guard with it. Re-exported here for the
# existing ``from genesis_contracts import EgressGate, PrivateDataEgressError``
# call sites (genesis_pipeline / meta_initiative_deriver / roster_proposer).
from mcs_egress import EgressGate, PrivateDataEgressError  # noqa: F401,E402
