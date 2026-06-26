---
name: mcs:<project>:ramble
description: The front-end of the steering trio — the founder just speaks their mind for as long as they like about where they are, where they're trying to get, and what they're clarifying; it atomic-decomposes the ramble, runs a back-and-forth Q&A until questions hit marginal-negative value, folds everything into the plan, repopulates the task board, and ends with the autonomous-run handshake. Operates on the company brain at $BRAIN_ROOT from any repo.
---

# ramble — speak your mind, then we move

> **Namespace.** This skill is addressed as `mcs:<project>:ramble`, where `<project>`
> is your project slug (resolved by `mcs_namespace.qualify("ramble")` — first hit
> wins: explicit arg > `$MCS_PROJECT` > config `project_slug` > the brain-root
> folder name). The `mcs:` prefix keeps a clone's skills from colliding with the
> founder's other Claude Code skills.
>
> **Paths.** The brain lives at `$BRAIN_ROOT` (resolve via `mcs_paths.brain_root()`
> — `$MCS_BRAIN_ROOT` > config `brain_root` > `$REPO_ROOT/brain`). Never hardcode a
> home path. All `ops/exchange/...` references below are relative to `$BRAIN_ROOT`.

The connective tissue between `vision`, `manifest`, and the autonomous run. The founder
talks — freely, as long as they want — about where they are, where they're going, what
they're trying to clarify. This skill turns that into structured progress.

## The flow
1. **Listen + atomic-decompose** — take the whole ramble and break it into atoms (problems,
   decisions, facts, new tasks, re-sequences, deletions). Run `mcs:<project>:atomic-decompose`
   on what was said.
2. **Q&A dialogue FIRST** — a genuine back-and-forth: ask the questions that would most
   improve output quality and accelerate self-improvement — **but only while each question
   has positive marginal value.** Stop the instant another question would have a
   marginal-negative impact on the cooperation. *(Learn that limit over time — with each
   ramble, get better at knowing when to stop asking. Over-asking is the failure mode to
   optimize away.)* Apply the **clarify-gate** (root `CLAUDE.md` §2.1): ask one line only when
   a wrong guess is expensive (rework / a fan-out / an external artifact); if cheap and
   reversible, fold the assumption in and keep moving.
3. **Fold into the plan** — write the new understanding into the brain (the owning agents'
   wikis / the pillars / the decision queue); **repopulate the task board** — add, embellish,
   delete, and re-sequence tasks/projects/subprojects so the board reflects the new thinking.
4. **The handshake** — when there are NO more positive-value questions, ask **'go'**; the
   founder says 'go'; then ask **'confirm?'**; the founder says 'confirm' → launch (or extend)
   the autonomous loop with the freshly-folded work.

## Mid-workflow
If an autonomous run is already in flight, a ramble doesn't restart anything — its folded
output is **just another unit added to the running loop-until-dry stretch** toward the
meta-initiatives and the vision. No full handshake needed; drop it in and keep going.

## Owner routing (base-roster template — genesis proposes the rest)
When the ramble decomposes into work, route each atom to its owning agent. Ship with the
**base roster** only — fill the rest from your own corpus via the genesis roster proposer
(`roster_proposer.py`, the `≥ 3 distinct anchored signals` floor):
- **Coordinator / chief-of-staff** — orchestration / cross-cutting / roster items; the
  default ONLY for genuinely cross-cutting work (do not over-route here).
- **Specialist A** (e.g. legal/business), **Specialist B** (e.g. product), **Specialist C**
  (e.g. software/build) — route to the single domain owner when one is nameable.
- **operator** — anything only the founder can gate (a draft to review, a rule/fact to
  ratify, a send/external commitment to authorize, a design to finalize).

## Rails
- **Proposals only** at the board: ramble repopulates the plan, but nothing the founder must
  personally gate (a send, an external commitment, a money move) happens here.
- **Sanitized + local:** fold derived understanding, never raw private message/transcript
  bodies, secrets, or PII into any committed file. Local commits OK; never push unasked.
- The four hard limits (root `CLAUDE.md` §4) apply unchanged.
