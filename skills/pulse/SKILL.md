---
name: mcs:<project>:pulse
description: The unified pulse + close-out ritual (parts 2-4) for any working session — drop a sanitized pulse per active lane, update the owning agent's wiki log, run the skills-and-learnings retrospective, and emit SKILL-DELTA proposals routed to the owning agent. Use at the end of any substantive session, or as the cron WRITE half over a pulse-sweep signals JSON. Operates on the company brain at $BRAIN_ROOT regardless of cwd.
---
# pulse — the unified pulse + improvement ratchet (parts 2-4 of the close-out)

> **Namespace.** Addressed as `mcs:<project>:pulse` (`mcs_namespace.qualify("pulse")`).
> **Paths.** Brain at `$BRAIN_ROOT` (resolve via `mcs_paths.brain_root()`); resolve every
> `ops/exchange/...` path there — you may be in another repo. Never hardcode a home path.
> `<date>` = today, `YYYY-MM-DD`.

The close-out 4-part ritual = (1) `terminal-handoff` [its own skill] · **(2) pulse · (3) wiki
updates · (4) skills-&-learnings retrospective** — parts 2-4 are THIS skill.

## The simple operator contract
For the founder, this is the ONLY session close-out command. They should not need to remember
`fold`, git push rules, watermarks, or session-audit routines. `pulse` owns that complexity and
ends with exactly one plain-English state:
- `remembered, folded, synced` — pulse exists, fold either completed or has no backlog, and the
  brain is safely on `origin/main`.
- `remembered, fold/sync pending` — pulse exists, but fold is heavy/pending or the branch cannot
  be safely synced right now.
- `blocked` — pulse could not be written or a git sync would risk losing/overwriting another
  session's work; write a blocker artifact and say the one next action.

The founder's daily loop is `morning` → the autonomous-run command for background work → `pulse`
when a session did real work. Everything else is internal machinery unless `morning` surfaces it
as a blocker.

## Two modes
- **Interactive close-out** (a session ending): you already know what you did — go straight to
  the four parts below for THIS session's lanes.
- **Cron WRITE half** (unattended, over a pulse-sweep output): read the latest
  `ops/exchange/pulses/.signals-<date>.json` (the GATHER artifact — git deltas + session mtimes,
  already sanitized, paths-only). Synthesize one pulse per active repo/session in it. GATHER
  never opens message bodies; WRITE must not either.

## Part 2 — the pulse(s)
For each active lane this session (or each active repo in the signals JSON): drop
`ops/exchange/pulses/<date>-<slug>.md` — frontmatter (`pulse`, `terminal`, `status`, `anchors`)
+ an honest what-happened (done / partial / missed / gated). Sanitized: commit subjects + file
paths OK; **never raw private message/transcript bodies, secrets, or PII**. One pulse per lane,
not one giant pulse.

## Part 3 — wiki updates
For each owning agent touched, append a one-line pointer to their
`<agents-root>/<agent>/wiki/log.md` (the coordinator's for cross-cutting work): date · what
landed · the pulse/handoff anchor. Pointers, not prose — the pulse is the record.

**STANDING RULE: the product-owning agent is updated on EVERY pulse that touches the digital
product** — not only when "product" work was the lane. Any session that ships/changes app
features, user-facing surfaces, coach/assistant capability, data the user sees, or product
decisions gets a product-agent pointer framing the PRODUCT meaning: what the user can now do /
what progress the digital product made, with the pulse anchor. (Software mechanics still go to
the software agent; the product agent gets the product-progress view of the same work.)

## Part 4 — skills & learnings retrospective + the SKILL-DELTA emitter (the ratchet)
This is what makes the system self-improving. For the session, capture:
- **Skills UPDATED** this session (what + why).
- **Skills that SHOULD have been** updated → emit a **SKILL-DELTA proposal** per owning agent:
  the owning agent drafts the diff for skill X (`<the learning>`) → **operator one-pass review →
  apply** (skills are operator-owned scaffold; the gate stays). Write these as a
  `## SKILL-DELTAS` block in the pulse AND file them into the ledger (Part 4b).
- **What each agent needs to develop** from this session's findings (queue per agent).
- **Meta-lessons** (robustness / autonomy / efficiency / quality).

### Part 4a — THE MUSK-REFLECTION GATE (operator-CRITICAL)
Run this EVERY pulse, with a **strict adversarial honesty-check — neither overblown nor
underblown** (an inflated mea-culpa is as useless as a whitewash; name the real misses and only
the real misses, against what actually happened this session). Answer all three, in order:
1. **Where did I fail to apply Musk's algorithm?** Name each spot I optimized/built/automated
   something that should have been **questioned (step 1) or deleted (step 2)** first — a
   requirement I took as given without an owner's name on it, a thing I polished that shouldn't
   exist, an automation I added to a broken step. For each: **what did it cost** (the real
   consequence — reversed work, wasted effort, a bloated artifact)?
2. **Where was I short on quality, efficiency, accuracy, or helpfulness?** The honest one of the
   four (or more): a claim I asserted without verifying, a slower path I took, a deliverable that
   wasn't elite, a place I answered the letter but missed what the founder actually needed. Cite
   the concrete instance.
3. **What skill change prevents recurrence?** For at least one miss above, name the **CONCRETE,
   NAMED skill-improvement** — *which skill file, which step, what it should now say* — not "be
   more careful." This is the load-bearing output: a reflection that produces no named delta has
   failed this gate. (If a session genuinely had no miss worth a delta, say so explicitly and say
   why — that itself is the honesty-check passing, not a skip.)

### Part 4b — FILE EVERY DELTA INTO THE LEDGER (close the loop — nothing dies in a pulse)
Every SKILL-DELTA this pulse emits (from Part 4 OR the Musk-reflection gate) gets ONE row in
`ops/exchange/skill-deltas-ledger.md`, `status: OPEN`, routed to the SPECIFIC owning agent —
*before* you close out. **Recurrence check first:** grep the ledger for a prior OPEN/RESOLVED row
on the same skill + same root cause; if one exists, **do NOT file a duplicate — escalate that
row** (bump `priority`, increment `recurrence: Nx`, append this anchor). The ledger is what
`morning` surfaces as the open-count and what makes APPLIED-vs-REJECTED trackable; a delta that
only lives in the pulse body is the exact failure this ledger exists to stop.

**DRAFT-AT-CAPTURE — file the EDIT, not just the intent.** Filing a row `status: OPEN` is not
sufficient on its own. For every NEW delta this close-out emits, also **draft the concrete edit**
— exact target file + the anchor (current text) + the new text — staged in
`ops/exchange/skill-deltas-drafts-<date>.md`, and point the ledger row's `resolution` at it
(`DRAFTED <date> — <draft path>; awaiting bless`). AND **sweep the OPEN ledger**: any
pre-existing OPEN delta still lacking a staged draft gets one drafted now (or an explicit blocker
noted) — so **no OPEN delta crosses a close-out undrafted**. AND **attempt ratification
in-session**: where the founder is present, present the drafts in plain English for bless; on
bless, APPLY (pre-image + registry row + `status: APPLIED`). An OPEN row with no draft is a vague
to-do that rots; an OPEN row WITH a staged edit is one operator-bless from applied.

### Part 4c — IN-THE-MOMENT CAPTURE (a callout becomes a tracked delta the instant it happens)
This does not wait for close-out. The moment **the founder or another agent calls out** a
skill/process shortcoming — "that should be a skill change," a correction to how you worked, a
repeated friction — file it into `ops/exchange/skill-deltas-ledger.md` `status: OPEN`
**immediately**, `born: <date> · in-the-moment-callout · <anchor>`, with the same recurrence
check (escalate a match, don't duplicate). An unfiled callout is a lost callout.

**The entrypoint:** the moment a substantive correction lands, call
`loop/skill_deltas.py` `capture_correction(...)` — it files the `proposed` delta in-conversation
(no close-out wait), with the same recurrence/escalation and operator-gated guarantees as `capture`.

### Part 4d — COMPREHENSIVE, never terse-+-"check the board" (the swarm/overnight report default)
After any **swarm / overnight / multi-workstream** completion, the report to the founder DEFAULTS
to the **full comprehensive plain-English "here is everything that has been accomplished" brief**
(a **Type-2 / FOR-THE-OPERATOR** doc): organized by what they care about, listing everything that
landed, every "why" a real-world consequence (the "translate, don't inform" rule), THEN a pointer
to the board for per-item drill-down. **NEVER substitute a terse ranked table + "that's what the
board is for" for the accounting** — that forces a low-technical founder to decode a board instead
of reading the plain brief they can act on. The board is for drill-down, not a replacement for the
brief.

## The self-sufficiency rule — the pulse CARRIES the detail, it does not distill it away
A pulse is a PRIMARY SOURCE: the handoff and the next session rely on it, so it must be
self-sufficient — the next session should NEVER have to go back and read this session's transcript
to recover what happened. So a pulse must CARRY the load-bearing detail, not summarize it out:
verbatim load-bearing operator directives, the WHY/HOW behind the work (not just the what), every
open thread's exact current state, and decisions + their rationale. Distilling the granularity
away is the exact failure this rule exists to stop — the GRANULARITY floor below is the
SOURCE-side floor that makes this hold.

## The relevance gate (do NOT over-route to the coordinator)
A SKILL-DELTA or wiki pointer routes to the SPECIFIC owning agent (a base-roster slot or a
genesis-proposed specialist — software, product, legal/business, design, etc.). Only genuinely
cross-cutting or roster/cockpit items go to the coordinator. If you can name a single domain
owner, route there.

## Owner routing (base-roster template — genesis proposes the rest)
Ship with the **base roster** only — **Coordinator / chief-of-staff** (orchestration /
cross-cutting) · **Specialist A** (legal/business) · **Specialist B** (product) · **Specialist
C** (software/build) · **operator** (the founder) — and let the genesis roster proposer add the
company-specific specialists from real evidence (`≥ 3 distinct anchored signals`).

## Rails
Cron/unattended mode: **DROP files only, never commit** (the cadence/capture-committer is the
brain's single committer — a cadence job that WRITES is a safety boundary, so the unattended lane
writes pulses/wiki-logs/skill-delta-proposals ONLY, never edits skills or pillars autonomously).
Interactive mode: local commits OK, **never push unasked**. No secrets/raw-private-bodies in any
pulse. SKILL-DELTAs are PROPOSALS — applying a skill edit is always operator-gated. The four hard
limits (root `CLAUDE.md` §4) apply unchanged.

## Fold hook
Stamp every pulse you drop with frontmatter `folded: pending` — the `fold` pass sweeps that
marker, folds knowledge into the owning agents' wikis, and restamps. Never wait on it at
close-out.

**AUTO-FOLD trigger.** After dropping this pulse, run a read-only fold-backlog check. If it
reports a fresh fold backlog, run the `fold` pass now on that backlog — folding the pulse you just
wrote into the owning agents' wikis instead of leaving it to pile up. It NEVER blocks the
close-out: a heavy/stalled fold just leaves the item in the backlog the next trigger re-surfaces.
Unattended: drop a body-free signal for the fold WRITE half.

### Brain sync gate — hide git complexity from the founder
After the auto-fold trigger, check the brain repo's sync state before reporting done:
1. Run `git fetch origin main`, then `git status --short --branch`.
2. If `main` is ahead-only and there are no staged/dirty changes unrelated to THIS close-out,
   and pushing is the founder's standing rule for this repo — push; otherwise leave it for the
   founder.
3. If `main` is clean and behind-only, fast-forward pull, then re-run the fold/sync check.
4. If `main` is diverged (`ahead N, behind M`) or dirty with unrelated files, DO NOT merge,
   rebase, force-push, stash, or ask the founder to understand the graph. Write a sanitized
   blocker note under `ops/exchange/sync-blockers/<date>-<slug>.md` with: local-only commits,
   remote-only commits, dirty paths, the safest next command for an engineer, and why it is
   blocked. End with `remembered, fold/sync pending` or `blocked`.
5. Never force-push, never discard the founder's changes, never include raw private content in
   the blocker.

The operator-facing report must be one line: `synced to main`, `sync pending: <blocker path>`,
or `sync blocked: <reason>`. No raw git lesson unless asked.

## GRANULARITY + OBJECTIVE floors
> Set the floor at the SOURCE (cheapest; fold inherits clean input):
> - **Pulse body floor.** The what-happened MUST cite concrete anchors of the work — commit
>   SHA(s)/range, changed file paths or file:line, exact numbers (test/row counts, build
>   numbers) — not prose summaries. "Did X, looks good" is not a pulse.
> - **Objective served.** Every pulse states in one line which company objective the lane
>   advanced (a meta-initiative / priority / north-star), or marks `objective: maintenance`.
