---
name: mcs:<project>:manifest
description: The ground-up product engine — from the most fundamental customer problems, do the maximum responsible research (user research, A/B framing, competitive/feature research, deep research, computer vision) → refine → spec → design → bring the vision to life as a PROTOTYPE. The bottom-up complement to vision. Produces units that feed the autonomous loop. Works from any repo; operates on the company brain at $BRAIN_ROOT + the product repos.
---

# manifest — bring the vision to life, ground-up

> **Namespace.** Addressed as `mcs:<project>:manifest` (`mcs_namespace.qualify("manifest")`).
> **Paths.** Brain at `$BRAIN_ROOT` (`mcs_paths.brain_root()`); product repos resolve from
> config. Never hardcode a home path.

The opposite of `vision`. `vision` clarifies the meta-initiatives top-down; `manifest` starts
at the **most fundamental customer problems** and works UP — through maximal responsible
research and understanding to a built prototype. Its north star: the deepest possible
understanding of **the customer persona, the business objectives, the technologies available,
and the most innovative design strategies — for maximal user pleasure, ease, simplicity, and
seamless power and effectiveness.**

## The pipeline (each stage maximally parallel; every artifact passes "will this genuinely help the founder?")
1. **Identify the clearest fundamental problems** to solve — grounded in the persona and the
   meta-initiatives (read the genesis decomposition + the meta-initiative tree first, so
   `manifest` EXTENDS the plan, never reinvents it). **A connected-concern is NOT a
   requirement (Musk step-1): before you spec or build anything, QUESTION it — "X relates to
   safety / X relates to Y" is not an operator requirement; cite the operator anchor that
   actually asked for it, or DROP it.** "The system needs it" is not an owner. This applies to
   the founder's and the orchestrator's *inferred* requirements too — questioning the
   requirement first (the cheapest Musk step) deletes the work before it exists.
2. **Research, maximally + responsibly** — user-research synthesis, **competitive + feature
   research**, A/B framing, and **deep research** (web + a deep-research harness), plus
   computer vision where it sharpens understanding. **Probe any tool/capability with a small
   bounded test BEFORE relying on it.** Honor the data-boundary rail (below) on every external
   research call.
3. **Refine** — what's truly net-new vs already-covered; the innovative, simplest, most
   effective angle.
4. **Spec** — a per-thread product spec, owned by the relevant agent (a base-roster slot or a
   genesis-proposed specialist).
5. **Design** — the house-bar design, design-reviewed for fidelity + merit by an independent
   reviewer (the twin-peer rule — a pair must not green-light itself).
6. **Prototype** — culminate in a prototype (in the prototype repo / the app on a branch).
   Build only when its dependencies are ratified; otherwise produce the prototype DESIGN spec
   and queue the build as the next unit.
7. **Reconcile + board** — map every output against the meta-initiatives/subprojects, flag
   net-new, update the task board with full provenance.

## How it runs (inherits the autonomous engine)
Maximally parallel: **probe concurrency, then isolate** (each parallel builder its own git
worktree); a single simulator means **parallel code-gen, serial visual-verify**. Goal-loop
per lane (build → an independent reviewer concurs as fact → close). Loop-until-dry. All the
autonomous-run rails + resilience apply (reversible-only; the four hard limits gated;
disk-reseed recovery on a transport drop; a liveness monitor). **Mid-run, a `manifest` is
just another unit added to the running loop-until-dry stretch.**

## The close-out
Every `manifest` output → the task board, per task, under its initiative → project →
subproject, with the responsible agent + the full provenance + the gated pile. The deliverable
is measurable progress toward a shipping prototype, plus the research foundation that makes the
next `manifest` unit sharper.

## Owner routing (base-roster template — genesis proposes the rest)
Specs/designs/prototypes route to the owning agent. Ship with the **base roster** only —
**Coordinator / chief-of-staff** · **Specialist A** (legal/business) · **Specialist B**
(product) · **Specialist C** (software/build) · **operator** — and let the genesis roster
proposer add the company-specific specialists from real evidence (`≥ 3 distinct anchored
signals`).

## Rails
- **Data-boundary (egress):** before any dispatch to a foreign / multi-vendor model,
  classify the data — a tight spec + non-private files only, never raw private content.
  Anything unclassifiable is treated as private and does not leave. (The `EgressGate` is the
  code enforcement of this rail.)
- **Reversible-only** autonomous work; build on a branch; local commits OK; never push unasked.
- The four hard limits (root `CLAUDE.md` §4) apply unchanged.
