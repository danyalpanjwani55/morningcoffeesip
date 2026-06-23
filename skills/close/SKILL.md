---
name: mcs:<project>:close
description: The end-of-day close-out for a working day — runs the full pulse close-out (pulse + wiki + Musk-reflection + skill-deltas + brain sync) AND THEN consolidates the day's branches into a clean main, so the overnight autonomous window starts from one truth line. The next entry point after close is always morning. Operates on the company brain at $BRAIN_ROOT regardless of cwd.
---
# close — close out the day (pulse + branch consolidation)

> **Namespace.** Addressed as `mcs:<project>:close` (`mcs_namespace.qualify("close")`).
> **Paths.** Brain at `$BRAIN_ROOT` (resolve via `mcs_paths.brain_root()`); never hardcode a
> home path.

`close` is the ONE end-of-day command. It is `pulse` PLUS the branch-consolidation `pulse`
deliberately refuses to do, so the founder ends the day on a single clean `main` instead of a
pile of stranded branches.

## The day cycle (why close exists)
```
morning  →  work + autonomous-run (background swarms on branches)  →  close
   ↑                                                                  │
   └──────────  overnight: cloud routines + autonomous-run  ──────────┘
                keep producing routine branches
```
`morning` opens the day by EVALUATING the overnight branch crop; `close` ends the day by
CONSOLIDATING the day's crop. The two bracket the autonomous window. Without `close`, each
scheduled routine run leaves a throwaway branch and `main` silently drifts — the failure mode is
dozens of stranded routine branches accumulating because an auto-merge only matched one
branch-name prefix, so the morning gate runs on stale data and a routine "compensates" by
messaging the founder directly.

## The simple operator contract
For the founder this is the only end-of-day command. They should not need to think about `pulse`,
`fold`, branches, or git. `close` ends with ONE plain-English state:
- `day closed — remembered, folded, synced; N branches consolidated, M flagged for cleanup` —
  pulse written, fold clean/backlogged-noted, `main` clean on origin, the day's substantive
  branches merged.
- `day closed — sync/consolidation pending: <one next action>` — pulse written but a branch could
  not be safely merged (conflict) or sync is blocked; the blocker artifact path is named.
- `blocked — <reason + one next action>` — pulse could not be written or consolidation would risk
  losing another session's work.

## Step 1 — run the full pulse close-out
Run `mcs:<project>:pulse` exactly as defined (its parts 2–4): the per-lane pulse, the wiki
pointers (incl. the product-agent-on-every-product-touch rule), Part-4 skills-&-learnings + the
Musk-reflection gate, the skill-deltas ledger filing + draft, the comprehensive-not-terse report,
and the brain sync gate. **Do not duplicate or re-implement `pulse` — invoke it.** Everything
`pulse` owns (sanitization rails, no-raw-bodies, the one-line status) is inherited here unchanged.

> `pulse`'s sync gate, by design, writes a sync-blocker and STOPS on a diverged/dirty `main`
> rather than merging (it must never risk another session's work unattended). Step 2 is where
> `close` — founder present — actually resolves that divergence.

## Step 2 — branch consolidation (the automerge pulse won't do)
The founder is present, so `close` may do the merges `pulse` defers. Reconcile the day's
cloud/work branches into `main`:

1. **Enumerate.** `git fetch origin --prune`; list unmerged routine/work branches
   (`git branch -r --no-merged origin/main` filtered to the routine prefix). (There may be
   dozens — they are mostly per-run snapshots.)
2. **Classify each** by newest-commit date + commit subject + `git diff --stat origin/main...<branch>`:
   - **Substantive** — real un-landed work: meeting reconciliations, folded deltas, proposed
     actions, pillar edits. KEEP for merge.
   - **Throwaway / superseded** — no-op or stale per-run snapshots (subjects like "watermark
     advance only", or an OLDER run of the same routine a NEWER branch supersedes). Do NOT merge
     — flag.
   - **In-flight** — a branch a live autonomous session is still writing (very recent activity +
     an active matching session). Do NOT merge mid-write — leave it for the next `morning`.
3. **Merge the substantive ones into `main`**, newest-representative-per-routine first, **one
   branch at a time, ABORT-ON-CONFLICT per branch**. On a conflict you can resolve cleanly,
   resolve by **union of real content / operator-tier-wins / supersede-with-archive**; on a
   conflict you cannot, abort that branch, leave it untouched, and FLAG it — never force, never
   reset, never rebase shared history.
4. **Push `main`** (the founder's standing rule for this repo).
5. **Cleanup is FLAG-ONLY (hard-limit #4 — branch deletion is operator-gated).** List the merged
   + throwaway branches and offer the one-command prune for the founder's explicit go. **Never
   auto-mass-delete branches.**

## Step 3 — hand to morning, don't kill the autonomous run
`close` leaves a clean, synced `main` and a one-line summary: what consolidated, what's flagged
for cleanup, and **which autonomous sessions are still running in the background** (they keep
working — `close` does NOT stop them; their branches get evaluated at the next `morning`). The
next entry point is always `morning`, which assumes overnight branches exist and evaluates them.

## Rails
- Inherits every `pulse` rail (sanitization, no raw private bodies, no secrets, the four hard
  limits).
- **Abort-on-conflict, never force.** No force-push, no reset, no history rewrite, no rebase of
  shared branches.
- **Never auto-delete branches** — flag for the founder's go (hard-limit #4).
- **Never merge a branch a live session is writing.**
- Everything reversible: each consolidation is an ordinary, revertible merge commit.
- The four hard limits (root `CLAUDE.md` §4) apply unchanged. **One sharp edge, stated plainly:**
  a "don't push" gate cannot hold on a shared `main` — so the discipline is a separate branch, or
  you state openly that the gate is unenforceable.
