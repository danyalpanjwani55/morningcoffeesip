---
name: mcs:<project>:atomic-decompose
description: Decompose ONE artifact — a sanitized ingest event (chat/email/meeting thread), a meeting transcript, OR a session transcript — into every explicit AND implied atomic task/decision (owner · horizon · done-bar · anchor), emitted as `proposed` entries to the action queue for the morning gate. Reads the SANITIZED layer by default; an operator-directed Authorized raw meeting transcript mode may read a raw MEETING transcript privately to derive facts but NEVER commits raw bodies. Operates on the company brain at $BRAIN_ROOT regardless of cwd.
---

# atomic-decompose — artifact → proposed atomic tasks

> **Namespace.** Addressed as `mcs:<project>:atomic-decompose`
> (`mcs_namespace.qualify("atomic-decompose")`).
> **Paths.** Brain at `$BRAIN_ROOT` (resolve via `mcs_paths.brain_root()`); resolve every
> `ops/exchange/...` and `ops/scripts/...` path there. Never hardcode a home path. `<date>` =
> today, `YYYY-MM-DD`.

**Why.** Breaking work to the atomic level is what keeps the action queue fed without hand-entry.
Every new thread/transcript continuously mines its own atomic tasks into the action queue →
`morning` gate → the task board. It is the engine that keeps the queue alive.

**What it is NOT** (the anti-duplication boundary):
- NOT source-discovery — it reads ONE supplied artifact; the ingest refresh finds deltas.
- NOT a ratifier — `morning` owns ratification → the board. This only proposes.
- NOT a closer — a resolution-detector closes obligations. This only opens proposals.
- NOT a pillar/wiki writer — that's `fold` + the synthesis writer.

## Input — ONE artifact reference (SANITIZED layer by default; raw meeting transcript only under the authorized mode below)
- a sanitized ingest event JSON under `ops/exchange/<lane>-events/<ts>-<hash>.json` — read its
  `safe_summary` + the `asks` / `decisions` / `follow_ups` arrays (each `{summary,
  source_anchor}` = a proto-task).
- OR a sanitized meeting event + its `source_archive` ref (the fuller *sanitized* text).
- OR a session transcript (the `pulse` case — this session's own work; read the conversation, not
  tool-result blobs).
**Default — NEVER open raw private bodies** — they are off-git by contract (a private store + a
git-ignored archive). Do not fetch them in the default / unattended path. The sanitized layer is
body-free by construction; that is what makes this skill safe to run unattended (the hard limit
on raw private content). **Exception:** the operator-directed *Authorized raw meeting transcript
mode* (below) may read a raw MEETING transcript line by line — derive-only, never committed. The
message lanes (chat/email bodies) stay sanitized-only in every mode.

## Authorized raw meeting transcript mode (operator-directed, interactive ONLY)

The default sanitized-only rule keeps the **unattended** decompose-sweep safe. When the founder
directs a meeting reconciliation interactively (e.g. a transcript-reconciliation run, or `pulse`
over this session), this authorized mode applies — encoding the rule: *the rail is "raw
transcript bodies never enter git," NOT "do not read the transcript."* Reading only the sanitized
summary leaves the reconciliation incomplete — it cannot catch what the auto-summary (or a
cross-lane fold) missed.

```
Authorized raw meeting transcript mode:
read raw transcript privately line by line;
write only derived tasks, decisions, owners, dates, blockers, and anchors;
never commit raw transcript bodies.
```

- **Scope:** raw MEETING transcripts only (a meeting recorder / notes doc), reached via the
  connectors. NOT the personal message lanes — chat/email bodies stay sanitized-only.
- **Read privately, in full:** open and read the raw transcript line by line (in working memory /
  off-git scratch) to catch what a sanitized summary missed. Delete any off-git raw scratch when
  done.
- **Emit derived ONLY:** tasks · decisions · owners · dates · blockers · provenance anchors
  (meeting · timestamp or turn · source doc id · speaker if needed). Use anchors, never verbatim
  quotes.
- **Never commit / never expose raw bodies:** no raw transcript text in any committed file, log,
  or chat-visible output (hard limit #3 is absolute in every mode). `raw_private_content_committed:
  false`.
- **NOT for the unattended cron:** the `decompose-sweep` WRITE path stays sanitized-only; this
  mode requires a live, operator-directed run.

## Procedure
1. **READ** the sanitized artifact; pull its substance (the `asks/decisions/follow_ups` are the
   spine; the `safe_summary` is the context).
2. **DECOMPOSE into atomic tasks — explicit AND implied.** The *implied* judgment is the core
   differentiator: "we should reach out to partner X" → atomic tasks {identify the right contact ·
   draft the pitch · send · log}. Granularity bar: for a **buildable software/hardware feature**,
   split across the build cycle (Design → Backend → App → Integration → Test → CI/CD → Release);
   for **business / ops / research / legal** work, one atomic task = one discrete deliverable. Err
   toward more-atomic.
3. **TAG each task** with the action-queue node contract + the lane + the horizon:
   `id · title · owner · lane(operator|agent) · acceptance(ARTIFACT + checkable condition) ·
   state: proposed · next-step · blockedBy · parent(best-effort meta-initiative/project id) ·
   source-anchor(the artifact id) · horizon · board-id(blank)`.
   - **owner** via routing judgment (NOT keywords): route to the single domain owner — a
     base-roster slot (**Coordinator / chief-of-staff** for orchestration/cross-cutting ·
     **Specialist A** legal/business · **Specialist B** product · **Specialist C** software/build)
     or a genesis-proposed specialist; **operator** for anything only the founder can gate. Never
     invent a role from thin air; let the genesis roster proposer (`≥ 3 distinct anchored
     signals`) own role creation.
   - **lane** ∈ `operator | agent`. `operator` ONLY if the founder must personally gate it
     (review a draft · ratify a rule/fact · authorize a send/external commitment · finalize a
     design); else `agent` (the parallelizable background work pool). Default mirrors owner
     (`operator`-owned ⇒ `operator`, else `agent`) — but state it explicitly.
   - **horizon** ∈ `1wk · 2wk · 4wk · 3mo · 6mo · 12mo · 24mo` (when the work completes).
     Time-critical/unblocks → near; end-states → far.
4. **GROUND — keep/drop:** keep only a real, current, actionable obligation; DROP chatter / FYI /
   already-done / pure-context; ground every kept task in the artifact's `source_anchor` (never
   invent one); confidence < 0.6 → keep but tag `(needs-confirm)`, do not silently assert.
   **Dedupe** by deterministic id `atomic-<artifact-hash>-<slug>` AND against the existing open
   action queue — never re-propose an item already present.
5. **EMIT** the kept tasks as `state: proposed` appended to
   `ops/exchange/actions/<date>-open.md`, under a labeled block
   `## From <artifact> (atomic-decompose <date>)`. **DROP-only** in cron/unattended mode (the
   capture-committer is the brain's only committer); interactive local commit OK; never push
   unasked.
6. **REPORT:** counts (substance items → extracted → kept → dropped → deduped) + the source
   anchor + any `(needs-confirm)` items. **Never** ratify · **never** write the board · **never**
   send · **never** set `done` · **never** mutate pillars/skills.

## Two trigger modes
- **Ingest — the `decompose-sweep`** (two halves):
  - GATHER (pure, no-LLM, read-only, never commits): inventory sanitized events newer than the
    `.last-decompose-<date>` watermark across `ops/exchange/*-events/` → one signals JSON (paths +
    ids only, never contents).
  - WRITE (an unattended `--allowedTools "Read,Write"` run, sanitized input only): run THIS skill
    per new artifact → drop `proposed` tasks. Enabling the WRITE step on a schedule is
    **operator-gated** (an unattended job that *acts* is a safety boundary).
- **Transcript — wrapped in `pulse`** (Part 2b): at close-out (and in the pulse cron WRITE half),
  run this skill on THIS session's transcript → `proposed` tasks for the session's own work,
  alongside the pulse. Drop-only; inherits every rail below.

## Rails (inherited, non-negotiable)
- **Sanitized layer by default; raw MEETING transcript only under the authorized mode above** —
  never raw private *message* bodies, never fetch from the private store/archive; the
  operator-directed Authorized raw meeting transcript mode may *read* a raw meeting transcript
  privately to derive facts. In EVERY mode: nothing raw to a committed file, log, or chat (hard
  limit #3 — absolute); derive-only.
- **Proposals only** — every emitted task is `proposed`; NEVER auto-`open`/auto-`done`. `morning`'s
  ACTIONS gate ratifies → the board. **Nothing enters the board any other way.**
- **Drop-only / no-push** in cron; **never send, never execute, never mutate** pillars or skills.
- **Keep/drop bar** — most chatter carries no task; flooding the queue is the failure mode. When
  unsure, drop or `(needs-confirm)`, never pad.
- The four hard limits (root `CLAUDE.md` §4) apply unchanged.
