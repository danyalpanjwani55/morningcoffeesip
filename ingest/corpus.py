"""IngestedCorpus — the genesis ``Corpus`` the spine produces.

``run_genesis(corpus, ...)`` consumes anything implementing the genesis
``Corpus`` protocol: ``events_since(since) -> Iterator[Event]``. The synthetic
``InMemoryCorpus`` already satisfies it for the demo; this is its real-data
sibling — the same ``events_since`` semantics (``since="inception"`` => no lower
bound; an ISO date => only strictly-newer events; deterministic ordering by
``(observed_at, event_id)``), but holding Events the ingest pipeline produced
from a founder's actual sources.

Keeping a distinct type (rather than reusing ``InMemoryCorpus``) makes the seam
explicit at the call site — ``run_genesis(IngestedCorpus(events), ...)`` reads as
"genesis is now eating real ingest", and leaves room for the corpus to grow a
windowed/streaming backing later without touching genesis. Stdlib-only.
"""

from __future__ import annotations

import os
import sys
from typing import Iterable, Iterator

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GENESIS = os.path.join(_REPO_ROOT, "genesis")
for _p in (_REPO_ROOT, _GENESIS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from genesis_contracts import Event  # noqa: E402


class IngestedCorpus:
    """A list-backed genesis Corpus built from ingested Events.

    Conforms to the genesis ``Corpus`` protocol structurally (it has
    ``events_since``), so it is directly accepted by ``run_genesis`` and by
    ``propose_roster``. Ordering is deterministic so a genesis run over the same
    ingest is reproducible.
    """

    def __init__(self, events: Iterable[Event]):
        self._events: list[Event] = list(events)

    def events_since(self, since: str) -> Iterator[Event]:
        ordered = sorted(self._events, key=lambda e: (e.observed_at, e.event_id))
        if since == "inception":
            yield from ordered
            return
        for event in ordered:
            if event.observed_at > since:
                yield event

    def all_events(self) -> list[Event]:
        return list(self._events)

    def __len__(self) -> int:
        return len(self._events)
