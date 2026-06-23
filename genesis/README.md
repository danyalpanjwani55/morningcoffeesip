# genesis/ — claim resolver + intelligence layer

Self-contained, stdlib-only genesis-engine code for the generalized
repo. Two slices live here:

- **The claim resolver — `genesis_resolver.py`**: when two facts about the same
  thing disagree, decide which is *current* (operator > primary > secondary;
  newer wins within a tier), archive the loser (never delete), and only mark a
  genuine `disputed` when two same-tier facts clash. Pure + deterministic.
- **The intelligence layer** (`genesis_pipeline.py`,
  `meta_initiative_deriver.py`, `roster_proposer.py`, `review_surface.py`,
  `genesis_contracts.py`): after the brain is populated, propose the
  meta-initiatives + agent roster + an operator review screen. Every proposal
  cites >=1 anchor or it's dropped; nothing auto-applies; nothing private
  leaves to a foreign model (the `EgressGate` rail); the operator-facing packet
  is plain-English ("In plain terms").

## Run

```sh
pytest -q                       # all tests (resolver + intelligence)
python genesis_resolver.py      # resolver demo on sample_claims.json
python genesis_pipeline.py      # full-pipeline demo (canned LLM, in-memory corpus)
```

> If your `python3` has a broken `pyexpat` (some newer builds do, which stops
> `pip`/`pytest` from starting), use the system interpreter instead:
> `/usr/bin/python3 -m pytest -q`. The code uses
> `from __future__ import annotations`, so it runs on Python 3.9+ despite the
> 3.10-style type hints.

## The one-line seam (drop the resolver into the populator)

In `claims_for_subject(subject, corpus)`, replace the final
`return detect_conflicts(dedupe_claims(claims))` with:

```python
resolved = resolve_claims(dedupe_claims(claims))   # from genesis_resolver
return detect_conflicts(resolved.kept)             # now only sees same-tier ties
```

Write `resolved.archived` to the pillar's archive section (archive-don't-delete).

## Rails (enforced, not aspirational)

- **Data-boundary (SDL-23)** — every foreign-model prompt passes
  `EgressGate.guard()`; secrets/PII/contract bodies / unclassifiable text →
  `PrivateDataEgressError`.
- **Verify-before-relay (SDL-19)** — a proposal with zero resolvable anchors is
  dropped before it reaches the operator.
- **Proposals-only** — `run_genesis` writes only under `genesis/out/` and emits
  only `status="proposed"` artifacts; the `ReviewPacket` is the gate surface.
- **Determinism where it counts** — all control flow + validation are pure and
  tested; only the injected `LLM` is non-deterministic, isolated behind a
  protocol (tests inject a stub).
