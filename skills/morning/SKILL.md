---
name: mcs:<project>:morning
description: The founder's first interaction of the day — present the open decision queue for rule/bless, any overnight in-review work for bless, the ACTIONS gate (the prioritized operator-lane day pulled from the action queue), AND last night's task proposals for ratify/edit/reject; push ratified tasks to the board (founder present = the gate); record rulings so proposals improve. Nothing enters the board any other way. Run-anytime: safe to re-run the same day — a later run shows only the DELTA since the last run and skips items already ruled. Operates on the company brain at $BRAIN_ROOT from any repo.
---
# morning (the operator gate)

> **Namespace.** Addressed as `mcs:<project>:morning` (`mcs_namespace.qualify("morning")`).
> **Paths.** The brain lives at `$BRAIN_ROOT` (resolve via `mcs_paths.brain_root()`); the
> `ops/exchange/...` and `ops/scripts/...` paths below are relative to it. Never hardcode a
> home path. `<date>` = today, `YYYY-MM-DD`.

## Simple operator loop
The founder should only have to remember three commands:
- `morning` — what matters now, what is broken, what needs a decision.
- the autonomous-run command — background investigation / swarm work.
- `pulse` — close a session so the brain remembers, folds, and syncs it.

Everything else (`fold`, git push/pull/rebase, session pulse audits, transcript lane
watermarks, skill-delta filing) is internal machinery. If one of those internal steps fails,
this skill surfaces it as a plain-English blocker with one next action; it must not make the
founder inspect raw git state or remember which maintenance command to run.

0. **LANE-HEALTH ALARM — the FIRST thing, before anything else.**
   Before the decisions gate or any task walk, check the refresh plumbing is actually alive
   AND the brain's own memory plumbing hasn't silently rotted — surface any failure LOUDLY
   for immediate fix. A frozen lane silently rots every downstream brief; a broken recall path
   silently rots every session's context. Two INGEST detectors + a three-part RECALL scan:
   - **(a) Frozen ingest lane.** Compare the newest event-file mtimes across the source lanes
     (the locally-driven lanes + any cloud refresh outputs). A lane whose newest file is
     conspicuously older than its peers is frozen. CRITICAL email check: read the
     brain-refresh watermark and compare `clock_scanned_through` vs `substance_folded_through`
     — **if the clock advanced but substance did NOT, the email lane silently failed** (it kept
     scanning timestamps forward while folding zero substance).
   - **(b) Non-firing brain-refresh routine.** Confirm any scheduled brain-refresh routine
     actually ran on its cadence (its refresh commits are landing on schedule). A routine that
     stopped firing = no fresh substance at all.
   - **(c,d,e) RECALL-HEALTH SCAN** (read-only, deterministic):
     - **(c) Fold-backlog.** Pulses written but never folded into an agent wiki
       (`grep -L "folded:" ops/exchange/pulses/*.md`) are write-only memory — the work
       happened, no agent's boot will see it. A handful is normal mid-day churn (surface); a
       large pile = the fold loop stopped → HALT, run `fold`.
     - **(d) Routing-map path integrity.** The context router is the FIRST read of every
       session; a path in the routing map that no longer resolves silently degrades every
       session hitting that intent. Any dead path (or a malformed map) → HALT (the map is
       DATA — repoint/remove the dead path).
     - **(e) Agent-wiki orphan/staleness sweep.** Retrieval is index-navigation, not RAG, so a
       `wiki/` page the wiki's own `index.md` doesn't link is UNREACHABLE — invisible to the
       booting agent. Reported as a count + worst offenders (surface — a wiki-hygiene backlog).
       Plus a peer-relative staleness read: a wiki whose newest page is conspicuously old has
       gone quiet.
     - **(e2) Oversize-page canary.** A wiki page that outgrew the bounded-write cap is an
       unnavigable pile (the flat-index ceiling). Run `scan_oversize_pages` (`loop/recall_health.py`)
       over the `$BRAIN_ROOT/wiki/` tree; report any page over the cap as a count + worst
       offenders — surface (a hygiene backlog, NOT a HALT; the page splits losslessly when next
       written, per 2.1's lossless cap).
   - **(f) Session/sync debt.** Surface only counts: sessions needing a pulse, sessions
     needing human review, unfolded pulses, and sync blockers (read any open
     `ops/exchange/sync-blockers/*.md` and show it as a one-line item). This collapses the
     "session audit", "missing-pulse detector", and "git ahead/behind" checks into one
     operator-visible debt line.
   - **(g) Routine-sent message (rail-breach watch).** Sweep any SENT mail / external-send log
     for a message a cloud/automation routine sent AUTONOMOUSLY — a self-addressed digest /
     "N items need you" message, or any SENT item with no human-session author. An unauthorized
     autonomous send is a hard-limit-#1 breach → LOUD (what · when · the one-line fix: the
     routine config lives in the cloud account, NOT in the repo or on the machine, which is why
     no local check sees it). The repo skills already forbid sends, so a recurrence means the
     cloud routine config diverged from the repo — detection is the only catch.
   If any detector trips at HALT severity (a frozen lane, a non-firing routine, an
   over-threshold fold-backlog, or a broken routing-map path): STOP and surface it at the TOP
   of the gate as a LOUD alarm (what · since-when/how-many · the one-line fix) so it gets fixed
   before the founder reads a single brief built on stale data or a session loads broken
   context. Surface-severity recall findings get ONE summary line — a backlog to drain, not a
   blocker. Clean = one line "lanes fresh, refresh firing, recall healthy" and proceed.

0.5. **RUN-ANYTIME — first-run vs delta-run gate (runs right after lane-health).** `morning`
   keeps its name and its full first-run behavior, but it is safe to re-run the same day — a
   midday re-open (new proposals landed, an overnight unit just finished, a decision got
   unblocked) must NOT re-present everything the founder already ruled this morning; that would
   burn their short window re-reading settled items. **No new state file** — the detector reads
   the date-stamped artifacts the skill ALREADY writes for today's `<date>`. Procedure:
   - **Detect.** This is a **DELTA-RUN** if ANY of today's run-markers already exists (the
     skill wrote them on an earlier run today): `ops/exchange/task-proposals/rulings/<date>.md`
     · `ops/exchange/decisions/<date>-resolved.md` · `ops/exchange/actions/<date>-done.md` ·
     the `ops/exchange/morning-gate-<date>/` brief dir. If NONE exist → **FIRST-RUN**: behave
     exactly as steps 1–7 below, nothing changes. (Keying off the *set* means one un-written
     path doesn't misclassify the run.)
   - **On a DELTA-RUN, print the delta header first:** one line — `delta-run · last run today
     ~HH:MM (newest run-marker mtime) · showing only what changed since`. Then run the same
     gates, each **scoped to the delta**. The "already-ruled set" = every id/slug recorded in
     today's run-markers. An item in that set is **DONE for today — skip it silently.** Surface
     only: (a) items genuinely NEW since the last run, and (b) anything the founder explicitly
     **deferred/carried** last run (deferral ≠ resolution — a carried item correctly
     re-surfaces).
   - **If the delta is empty** → say one line "no change since ~HH:MM — nothing new to rule"
     and stop. Do not regenerate briefs or touch the board.
   - **Reversible + cheap:** this gate writes nothing of its own; removing this step restores
     pre-delta behavior exactly.

0.6. **OVERNIGHT RECONCILIATION — evaluate the night's branches + ANSWER FROM THE RECORD before
   asking anything. Runs after lane-health, BEFORE every gate that reads `main` or asks a
   question.** The autonomous window runs BETWEEN `close` and `morning`, so ASSUME `main` does
   NOT yet hold the night's work and the inbox holds substantive overnight messages. Two legs,
   both before the gates:
   - **(a) Branch evaluation + consolidation (the `close` Step-2 consolidation, re-run over the
     overnight crop).** `git fetch origin --prune`; list the unmerged routine branches.
     EVALUATE each branch's progress (newest-commit · subject · `git diff --stat`): MERGE the
     substantive ones into `main` (newest-representative-per-routine, one at a time,
     **abort-on-conflict**, union / operator-tier-wins on a resolvable conflict, then push),
     FLAG superseded throwaways for operator-gated cleanup (**NEVER auto-delete — hard-limit
     #4**), and leave any branch a live autonomous session is still writing. **End-state:
     either everything merges into ONE clean `main`, OR a branch is DELIBERATELY kept separate
     with a one-line stated reason — never silently stranded.** The gates below read `main`; if
     this does not run first, every gate brief is built on stale data.
   - **(b) Answer-from-the-record sweep — NEVER ask the founder what the record already
     answers.** BEFORE writing any brief or asking ANY question, READ the primary record: the
     freshest meeting reconciliations (`ops/exchange/transcript-reconciliation/*.reconciled.json`
     + `ops/exchange/corrections/<date>-*.md`, on `main` after leg (a)) AND a same-morning
     inbox sweep for substantive real-person messages (skip receipts, newsletters,
     auto-replies, and the routine self-sends from step-0 (g)). Synthesize what they SAY into
     the gate. A `morning` that asks "what happened at meeting X / did Y happen?" when a
     reconciliation file or the inbox already answers it is a FAILED gate. The test: for every
     "what happened?" the gate would put to the founder, answer it from the record first;
     surface only the irreducible residual the record genuinely does not contain.

1. **Pull main**; read the newest `ops/exchange/task-proposals/<date>-proposed.md` (if none:
   say so, show yesterday's ruling summary, done). Also surface any `unpulsed work detected`
   flags from overnight refresh commits + anything decay-dated today.
1.5. **Skill-deltas open-count (the self-improvement backlog) — surface BEFORE the decisions
   gate.** Read `ops/exchange/skill-deltas-ledger.md`; count the `### SDL-*` rows under the
   `## OPEN` heading carrying `status: OPEN` (ignore the legend line and the `## RESOLVED`
   section — a raw `grep status: OPEN` over-counts). Show **one line**: the open count + the
   top 2-3 by `priority` (each: skill · one-line what · `recurrence: Nx` if >1x · owner). This
   is the close-the-loop surface — proposed skill changes used to die in pulses, so the count
   makes the backlog impossible to hide. The founder can, inline, **apply** one (→ owning agent
   drafts the diff; on apply, the row goes `status: APPLIED` + a registry row with a pre-image)
   or **reject** one (→ `status: REJECTED` + a one-line why). Default = carry. A row flagged
   `recurrence:` ≥2x (especially a re-opened APPLIED one) is a repeat offender — surface it
   first. Keep it to one screen; this is a backlog gauge, not a work session.
2. **Decisions gate (the rollup step) — present BEFORE the task walk.** Read the open queue
   (`ops/exchange/decisions/*-open.md`, `status: open`). Present each, one screen: question ·
   options + each option's consequence · owner's recommendation · what's blocked. Operator
   verbs: **rule / bless** → apply the change (supersede-with-archive the loser; mark the entry
   resolved + the source `disputed` wiki item resolved) · **needs-input** → spawn a task into
   the proposal list, keep the entry open · **defer** → carry. Triage: surface only BLOCKING
   disputes + bless-ready proposals; idle DRAFTs wait. Decisions go first — they often unblock
   the day's tasks. Ruled entries move to `ops/exchange/decisions/<date>-resolved.md`.
   - **Delta-run scope:** skip any decision already in today's `<date>-resolved.md`; present
     only decisions still `status: open` plus any new since the last run. A deferred/carried
     decision correctly re-surfaces.
   - **One brief per item (the gold standard).** For EACH decision/bless item that needs the
     founder's input, the gate produces an **INDIVIDUAL plain-English operator brief — one
     document per item** — written to `ops/exchange/morning-gate-<date>/NN-<slug>.md` (numbered
     `01`, `02`, … in present order). The founder is NOT handed one dense bullet list; they read
     + rule on each brief on its own. Each brief is a **Type-2 / FOR-THE-OPERATOR** document:
     opens **`## In plain terms`** → **`## What we did`** → **`## Your decision`** (the call;
     each option stated with its real-world consequence) → **`## My recommendation`** →
     **`## If you bless it`**; zero unexplained jargon, every "why" a real-world consequence
     (the "translate, don't inform" rule). The same one-brief-per-item discipline applies to
     the step-2b IN-REVIEW bless items. The short reading budget (step 6) is the founder's
     reading + ruling; **brief generation can fan out to subagents** (one per item) so the
     authoring cost never eats their window.
2b. **IN-REVIEW work walk (the overnight-loop bless gate) — present BEFORE new proposals, AFTER
   decisions. Runs only while the overnight loop is ACTIVE.** Read `ops/exchange/actions/*-open.md`
   for entries `state: in-review` (work the overnight loop finished + a reviewer certified,
   sitting on a branch). Present each one screen: title · branch ref · what-was-done · reviewer
   verdict · one-line bless-ask. Operator verbs: **bless** → the OPERATOR applies/sends/merges
   it (never the loop), mark the entry `done` · **correct** → capture the diff; the correction
   becomes a SKILL-DELTA (a rejection diagnoses the brain, not just the instance) + re-queue the
   unit · **defer** → carry. Blessing finished work is the back half of the cycle; ratifying
   proposals (step 3) is the forward half — the loop closes here.
   - **Delta-run scope:** skip any in-review unit already blessed-or-corrected this run-day;
     present only entries still `state: in-review` plus any newly certified since the last run.
2c. **ACTIONS gate — the operator's prioritized day. Present AFTER the in-review walk, BEFORE
   new proposals.** Read the action queue (`ops/exchange/actions/*-open.md`). **Meeting-derived
   first — the decay rule.** BEFORE the general sort, surface at the TOP of this gate: (1) any
   `proposed` action whose `source-anchor` is a meeting-transcript record (meetings decompose
   into the fastest-decaying tasks), and (2) any open transcript-gap / un-closed meeting (a
   `.reconciled.json` whose `meeting_closeout_status.atomic_decompose_done` is `false` is itself
   a line). **Especially highlight meetings + meeting-actions from the last 24–72h** — meeting
   action value decays quickly, so a 2-day-old uncaptured meeting decision is among the most
   urgent things in the queue. Then proceed to the normal walk. The queue is bifurcated by the
   `lane` field: this gate routes **`lane: operator` items to the FOUNDER** — the things only
   they can do (review a draft · ratify a rule/fact · authorize a send/external commitment ·
   finalize a design). Present their **prioritized day**, sorted by: (1) TIME-SENSITIVE /
   decay-dated TODAY first, then (2) horizon-nearness (`1wk` before `2wk` …), then (3) `state`
   (`in-progress` before `open` before `proposed`). For each, one line: id · title · the one
   operator verb it needs · blockedBy · the artifact the done-bar names. Operator verbs:
   **accept-new** (a `proposed` item greenlit → `open`) · **confirm-in-progress** ·
   **verify-done** (is the acceptance ARTIFACT actually real? — distrust-prior; never close on a
   claim) → move to `ops/exchange/actions/<date>-done.md` · **defer**. `lane: agent` items are
   NOT walked here (they are the background work pool — surfaced only as a one-line count "N
   agent-lane actions in flight," their completions arriving as decisions/in-review bless items
   above). **The two-document-type rule (step 7) applies: if any action needs a real decision,
   it gets a Type-2 brief like steps 2/2b; a verify-done/confirm one-liner does not.**
   - **Delta-run scope:** skip any `lane: operator` action already in today's
     `ops/exchange/actions/<date>-done.md`; present only still-`open`/`in-progress`/newly-
     `proposed` operator items plus any new since the last run.
3. **Walk the proposals** grouped by lane, one screen each: title · why-now · done= · blocked-on
   · effort · unblocks. Operator verbs per task: **ratify** · **edit** (capture the diff) ·
   **reject** (ask the one-word code) · **defer** (default-if-silent).
   - **Delta-run scope:** skip any proposal already ruled in today's
     `ops/exchange/task-proposals/rulings/<date>.md` (ratified→already on the board,
     rejected→settled); walk only proposals not yet ruled today plus any that landed in a newer
     `<date>-proposed.md` since the last run. A deferred proposal re-surfaces.
4. **Ratified → the task board** (founder present = the gate satisfied): the entry body = the
   task fields + brain anchor; write the board ID back into the proposal file; label by lane.
5. **Write the rulings file** `ops/exchange/task-proposals/rulings/<date>.md` (verbatim edits +
   codes) · update the suppression registry · append per-agent precedent one-liners to affected
   agent logs · flag stale-surface rejections as wiki-fix items. Commit (never push unasked).
   - **Run-anytime: APPEND, never overwrite.** A same-day re-run adds its new rulings UNDER a
     timestamped `## run HH:MM` sub-heading in the existing `<date>.md` (don't truncate the
     morning's earlier rulings — they ARE the already-ruled set the next delta-run reads). Same
     append discipline for `<date>-resolved.md` and `<date>-done.md`. The file's existence after
     run 1 is exactly what flips run 2 into a DELTA-RUN.
6. **≤10 minutes total.** If the founder starts editing >half the packet, STOP and ask what
   systemic miscalibration they're seeing — fold the answer into the proposal bars, not just the
   instances.
7. **The two-document-type rule (governs this skill AND generalizes).** Every deliverable is
   explicitly ONE of two types, and you state which: **Type-1 = FOR AI** (an agent reads it) —
   built to MAXIMIZE the reading-AI's deep understanding: technical, complete, dense,
   source-anchored, no hand-holding (the queues/ledgers). **Type-2 = FOR THE OPERATOR** (the
   founder reads it to review/bless) — built for them: plain English, ZERO unexplained jargon,
   opens `## In plain terms`, every "why" a real-world consequence (the "translate, don't
   inform" rule). **In `morning`, every brief from steps 2 and 2b is Type-2; the queues/ledgers
   behind them are Type-1.** This rule is NOT morning-only — it governs `pulse`,
   `terminal-handoff`, `fold`, and ANY agent producing an operator-facing document.
   - **Comprehensive-never-terse for a swarm/overnight summary.** When `morning` (or any
     close-out) reports a LARGE body of completed work — an overnight swarm, a multi-loop run,
     many board items — the default is the **full comprehensive plain-English "here is
     everything accomplished" brief**, organized by what the founder cares about, every "why" a
     real-world consequence, THEN a pointer to the board for drill-down. **NEVER substitute a
     terse ranked table + "that's what the board is for"** — the board is for drill-down, not a
     replacement for the accounting.
8. **AUTO-FOLD trigger.** At the end of the gate, run a read-only fold-backlog check (the same
   `folded:`-absent rule as `fold`). On a fresh backlog, run the `fold` pass now so what the
   night and this morning produced reaches the owning agents' wikis — instead of piling up
   unfolded until someone remembers `fold`. It NEVER blocks the gate (a stalled fold just leaves
   the item for the next trigger). Unattended: drop a body-free signal for the fold WRITE half.

## Owner routing (base-roster template — genesis proposes the rest)
Throughout the gate, route each item to its owning agent. Ship with the **base roster** only —
**Coordinator / chief-of-staff** (orchestration / cross-cutting; the default ONLY for genuinely
cross-cutting work) · **Specialist A** (legal/business) · **Specialist B** (product) ·
**Specialist C** (software/build) · **operator** (the founder) — and let the genesis roster
proposer (`roster_proposer.py`, the `≥ 3 distinct anchored signals` floor) add the
company-specific specialists from real evidence. Never re-propose a base-roster slot; never
invent a role from thin air.

## Rails
- **Nothing enters the board except through this gate** (founder present = the gate). No
  ratification any other way.
- **Abort-on-conflict, never force** on any branch consolidation (step 0.6a); **never
  auto-delete a branch** — flag for the founder's go (hard-limit #4).
- Sanitized: no raw private bodies / secrets / PII in any committed file, log, or chat output.
- Local commits OK; never push unasked. The four hard limits (root `CLAUDE.md` §4) apply
  unchanged.
