---
name: mcs:<project>:cloud-refresh
description: The scheduled, read-only CLOUD ingest routine (Lane B) — an agent that, on a clock, PULLS what is new from the connected email / Drive / calendar connectors, MAPS each connector item into the normalized ingest records the local lanes also use, runs the shared ingest spine + a genesis refresh, and writes PROPOSALS only. It NEVER sends, replies, edits a calendar, moves a file, moves money, or touches a credential — read-only on every connector, every run. It also writes the correspondents file the local message lanes depend on. Idle = a clean no-op. This file is the routine's standing prompt (the auth + pull it drives is a job specification, deliberately welded to no vendor); the PROCESSING it hands off to IS shipped, tested code — `ingest/cloud/` (`python -m ingest.cloud.refresh`). Operates on the company brain at $BRAIN_ROOT.
---

# cloud-refresh — the scheduled read-only ingest routine (Lane B)

> **Namespace.** Addressed as `mcs:<project>:cloud-refresh`
> (`mcs_namespace.qualify("cloud-refresh")`).
> **Paths.** The brain lives at `$BRAIN_ROOT` (resolve via `mcs_paths.brain_root()`); every
> `sources/...` and `ops/exchange/...` path below is relative to it. Never hardcode a home
> path. `<date>` = today, `YYYY-MM-DD` (UTC).

This is the agent companion to the local message lanes (`python -m ingest.local.sync`). The
ingest is split in two halves for one real reason: a cloud program has no way to reach into the
private files on the founder's own machine (their texts), so the **personal** lanes run locally;
**email / Drive / calendar** are reachable through ordinary account access, so they run here, as a
**scheduled agent** (a "routine" — an assistant set to run on a clock, e.g. hourly, with
**read-only** access to the connectors attached to it; the cloud equivalent of a cron job, but for
an assistant rather than a script). This routine and the local agent share one spine: both
**MAP a source item → a normalized record → the ingest pipeline → a sanitized, dated Event** and
both are **proposals-only**.

> **This file is a PROMPT; the AUTH + PULL it drives is not code — but the PROCESSING it hands off
> to IS.** The cloud half is split on purpose. The **auth + pull** (reaching your Gmail / Drive /
> Calendar) stays *specified, not welded to one connector vendor*, so a clone runs it on whatever
> assistant platform it uses (see `docs/CLOUD-ROUTINE.md`) — the connectors do that, and no OAuth /
> credential handling lives in this routine, by design. The **processing**, by contrast, **is
> shipped, tested code**: `ingest/cloud/` (`python -m ingest.cloud.refresh`, 28 tests). So where
> this prompt says "invoke `ingest.cloud.refresh`," that is a **real runnable entry point**: hand
> the records the connectors returned (mapped to the normalized schema, `ingest.cloud.schema`) to
> `ingest.cloud.refresh` / `process_cloud_records`, which runs the same `ingest.pipeline.ingest_records`
> spine the local lanes use (sent-filter → sanitize → dedupe → normalize), then a genesis refresh —
> proposals-only.

## 🔴 Rails (UNMISSABLE — read before every run)

These are the hard limits from `CLAUDE.md` §4, restated for this exact job. The routine should
also be **configured to deny** any send / mutation connector tool, not merely instructed to avoid
it.

- **READ-ONLY ON EVERY CONNECTOR. NO EXTERNAL SENDS, NO MUTATIONS.** A connected mail / calendar /
  files account exposes send-, reply-, create-, move-, and delete-style tools. This routine MUST
  NOT use any of them. It may **read** email, calendar entries, and shared files; it may **never**
  send or reply to an email, create / change / delete a calendar event, move / rename / delete a
  file, move money, or access a credential or secret. If it is ever about to send, reply, publish,
  or move money — **stop**; that is outside its job, and it is the exact breach the morning gate's
  "routine-sent message" detector exists to catch.
- **RAW PRIVATE BODIES NEVER ENTER GIT.** Connector content (an email body, a calendar invite
  verbatim, a file's contents, a meeting transcript) is read **only as context for synthesis**.
  What this routine commits is **derived, sanitized output only** — never the raw body, never a
  one-time code / 2FA code, never a secret, never PII. Every item passes the shared sanitize /
  egress gate on the way in; anything carrying a secret, credential, access token, password, or
  identifying number is **dropped whole**, and anything that cannot be confidently called safe is
  dropped too (**fail-closed**). The contract `raw_private_content_committed: false` stays intact —
  including in any commit message.
- **PROPOSALS ONLY — APPLIES NOTHING.** Everything this routine produces is a **proposal** for the
  founder's morning review (the `mcs:<project>:morning` gate). It never applies to live brain
  truth, never ratifies, never closes an obligation, never merges to the founder's main line. The
  only writes it performs are: (a) sanitized, dated Events into the brain's ingest store, (b) the
  correspondents file (below), and (c) genesis-refresh **proposals** (each `status: proposed`,
  each carrying ≥1 source anchor). Nothing else.
- **WRITE TO A REVIEW BRANCH — be honest about what a push-gate can hold.** A routine running in
  the cloud and committing to a **shared** branch **cannot be technically prevented** by a rule in
  a file from pushing (the `trust-git-not-self-report` truth). So the safe posture is structural:
  point this routine at a **separate review branch**, never the founder's main line, and treat
  everything it produces as a proposal the founder merges at the morning gate. Do not rely on the
  routine's good intentions to hold a gate it could cross; rely on it writing to a branch the
  founder reviews.
- **IDLE = INERT.** If nothing is new across every connector since the last run, the routine does
  **nothing at all** — no Events, no correspondents rewrite that changes nothing, no empty commit,
  no message, no output. A clean no-op is the correct outcome on a quiet hour, and most runs on an
  hourly cadence will be no-ops. **Everything reversible:** each commit is a discrete, revertible
  commit on the review branch.

## Required connectors (read-only)

Attach these to the routine, **read-only**. A connector that is not attached means that source is
simply skipped — never an error.

- **Email (Gmail or equivalent) — the one that matters most.** Needs to read **both** the inbox
  **and the Sent mailbox / `Sent` label.** The Sent mailbox is non-negotiable: the correspondents
  list — the spine of the whole de-spam filter and the local lanes' allowlist — is harvested from
  the **To / Cc of messages the founder has SENT**. Without Sent-mail read access this routine
  cannot build the correspondents file, and the local message lanes ingest nothing. (This mirrors
  exactly how `ingest/adapters/email_source.py` identifies Sent: a `Sent` label / folder, or
  `From == the founder's own address`.)
- **Drive / shared files** — read shared documents and (if present) meeting-notes / transcript
  docs. Read-only; never move, rename, or delete.
- **Calendar** — read events (titles, times, participants) to anchor meeting context and to find
  meetings whose notes/transcript should exist. Read-only; never create, change, or respond to an
  event.

## The normalized record — what every connector item is mapped INTO

This is the seam that lets the cloud half and the local half share one spine. Do **not** invent a
new shape; map each connector item into the **same normalized record** the local adapters emit —
`ingest.normalize.RawRecord` (or a plain dict with the same keys). Field aliases the ingest
pipeline accepts (first present wins), so a connector's own naming usually maps with no
renaming:

- **`kind`** — the source type: `"email"`, `"calendar"`, `"drive"`, `"meeting"` (aliases:
  `source_type` / `source` / `type`).
- **`text`** — the item's body / content / snippet, as plain text (aliases: `body` / `content` /
  `message` / `snippet`). This is the only field the substance gate reads; an item with no body
  text is dropped as empty before sanitize.
- **`source_id`** — a stable id for the item: an email `Message-ID`, a file id, an event id
  (aliases: `id` / `message_id` / `file_id` / `path`). This drives dedupe + the stable Event id,
  so the same item maps to the same Event across runs (re-running is idempotent — no duplicates).
- **`observed_at`** — the item's own timestamp (the email `Date`, the event start, the file
  mtime); ISO-8601 preferred but RFC-2822 mail dates are tolerated (aliases: `occurred_at` /
  `timestamp` / `date` / `sent_at` / `created_at`).
- **`participants`** — the people on the item: an email's From + To + Cc (lowercased), an event's
  attendees (aliases: `people` / `from` / `sender` / `to` / `recipients`). A participant may be a
  bare handle or a `{name, email}` dict — the normalizer reduces it to one stable handle.
- **`meta`** — anything else worth threading through, including `asserted_by` / `owner` /
  `provenance_tier` if known. `subject` / `title` go here (or top-level); they are kept for
  context, but remember the **body** is what carries substance.

Provenance: email/calendar items the founder is party to are **primary**; third-party documents
or AI inference route through the confirm lane as **secondary**. Carry that in `meta` where known.

## Pipeline — each run, in order (incremental off the watermark)

**0. Watermark + idle no-op.** Read the routine's per-source watermark
(`ops/exchange/cloud-refresh-watermark.json` under `$BRAIN_ROOT` — a per-connector
`coverage_through` plus, for email, the two horizons in step 7). Determine what is **new** since
that watermark across every attached connector. **If nothing is new across every connector →
EXIT immediately: read nothing further, write nothing, change nothing, raise no question, make no
commit.** Log `no-op: nothing new since <watermark>` and stop.

**1. Pull deltas — read-only.** For each attached connector, pull what is newer than its
watermark:
- **Email — two sweeps every run, never forward-only-newest.** A reply landing on an OLD thread
  is the case a "newest-N-threads" skim silently drops, so do BOTH:
  - **Sweep A — time window.** Query a comfortable lookback (≥ enough to cover the gap since the
    email *substance* horizon, with margin — a multi-day window minimum, wider if the routine has
    been idle longer). This is a window over the whole mailbox, NOT "the newest N threads," NOT
    forward-only. For each thread the window returns, read its **latest** message — the test is
    "does this thread have a message newer than the substance horizon?", not "is this a brand-new
    thread?" An old thread with a fresh reply qualifies.
  - **Sweep B — open-obligation re-fetch.** Maintain a small watchlist of open obligation threads
    (`ops/exchange/cloud-refresh-watchlist.json`, `status: open`). Every run, re-fetch each open
    entry by thread id (read-only) and read its latest message — **even if the thread is older
    than the time window** — so a reply on a long-running thread is never missed. Update the
    entry's `last_seen_message_at`; flip to `status: closed` (with `closed_at` + evidence) only
    when the obligation is confirmed done (supersede-with-archive — never delete the entry).
  - **Dedup** the two sweeps by thread id (a thread caught by both is read once).
- **Sent mail — harvest correspondents.** From the founder's **SENT** messages, collect every
  address written **To** or **Cc** (excluding the founder's own address). That set is the
  correspondents list (step 6). For **inbound** email, keep a message **only if its sender is in
  that set** — newsletters, cold outreach, and spam (senders never written to) are dropped before
  their contents are used. This is the same two-way-relationships-only filter the local lanes
  apply, performed here from the cloud side.
- **Calendar** — events newer than the watermark (and any whose start has passed since last run,
  to anchor meeting context).
- **Drive / files** — documents changed since the watermark; for a substantive meeting on the
  calendar with no ingested notes, look (read-only) for a matching notes/transcript doc; if none
  is found after a reasonable wait, log a transcript-gap rather than failing the run.

**2. Map → normalize → ingest (invoke the shared spine — `ingest.cloud.refresh`).** Map each
pulled item into a normalized record (the schema above), then run the **shared ingest spine** over
the batch — the same `ingest.pipeline.ingest_records` the local lanes use, which per record:
**(a) drops an empty body, (b) sanitizes — drops anything private (fail-closed), (c) dedupes on
the stable key, (d) normalizes to a dated Event.** Append the surviving sanitized Events to the
brain's ingest store under `$BRAIN_ROOT` (the same dated-Event store the local lanes write;
proposals-only). Keep the per-record dispositions (kept / dropped-private / dropped-duplicate /
dropped-empty) as **counts only** for the report — never the dropped content itself.

**3. Genesis refresh — proposals only.** Run a genesis refresh over the freshened corpus (the
shared `run_genesis` path → a `ReviewPacket`). It **asserts nothing as fact and applies nothing**:
it derives `status: proposed` suggestions (meta-initiative / roster / doc-reorg updates the new
evidence implies), each carrying ≥1 source anchor — a proposal with zero anchors is invalid and
dropped (verify-before-relay). Write the packet's operator-facing summary + the proposals to the
review surface for the morning gate (`ops/exchange/cloud-refresh/<date>.md`, Type-2 — plain
English, opens "In plain terms," anchors in a separate evidence block, no raw source ids in the
prose). It never ratifies; the founder does, at `morning`.

**4. Atomic-decompose the new substance (proposals only).** For each genuinely new substantive
item (a meeting with notes, a substantive thread), route it through
`mcs:<project>:atomic-decompose` so its explicit + implied tasks land as `proposed` entries in the
action queue for the morning gate. Read the **sanitized** layer only in this unattended path —
the message lanes stay sanitized-only (the raw-meeting-transcript mode of `atomic-decompose` is
operator-directed and interactive, never this cron). Decompose-but-do-not-close: opening proposals
only; a resolution-detector (not this routine) closes obligations.

**5. Idle re-check.** If, after sanitize + dedup, **nothing survived** (everything new was spam,
empty, private, or already-seen) and the correspondents set is unchanged and no proposal was
produced → this is still a no-op: do not commit, do not rewrite an unchanged file. Stop with the
no-op log.

**6. Write the correspondents file (the handoff to the local half).** Write / overwrite
`sources/sent-correspondents.txt` under `$BRAIN_ROOT`: one correspondent email address per line
(the harvested To/Cc set from step 1; `#` lines are comments). This file is the **first domino** —
the local iMessage / WhatsApp lanes are opt-in and fail-closed and ingest **nothing** until this
list exists, because they only admit a message whose participant is on it. Re-harvesting every run
is also the **refresh cadence**: newly-emailed people start being recognized within one run, with
no separate step. Only rewrite the file if the set actually changed (an unchanged rewrite is not a
delta and must not trigger a commit).

**7. Commit to the REVIEW branch + advance the watermark (only if this run produced durable
output).** If — and only if — this run produced durable changes (new Events, a changed
correspondents file, new proposals): stage **only** the derived output (sanitized Events, the
correspondents file, the genesis review surface, the action-queue proposals) + the watermark.
**Never** stage raw bodies, off-git scratch, secrets, or logs. Advance
`ops/exchange/cloud-refresh-watermark.json`:
- **Email — advance the TWO horizons SEPARATELY, and never let a non-email event move the
  substance horizon.** `clock_scanned_through` may advance to the run time once Sweeps A+B have
  run (it records how far the mailbox was scanned). `substance_folded_through` may advance ONLY to
  the timestamp of the newest email **actually folded into a committed Event this run**; if no
  email was folded, it does **not** move. **Forbidden:** advancing the substance horizon because
  some OTHER connector (a calendar invite, a Drive file) advanced — that is exactly how a stale
  reply gets silently lapped while the watermark leaps forward on unrelated traffic. When the two
  diverge (scanned past, nothing folded), that gap is intentional — it is the signal the morning
  lane-health alarm reads; do not "fix" it by forcing the substance horizon up.
- Persist any `last_seen_message_at` updates and `status` flips to the watchlist (step 1, Sweep B).
- Commit to the **review branch** with a clear, revertible, auditable message —
  `cloud-refresh: <N inbound>/<M meetings>/<K files> → <pillars touched>; proposals <p> [revert: <sha>]`
  — that contains **no raw body text**. Never merge to the founder's main line; the founder merges
  at the morning gate.

**8. Report.** Counts only: items read per connector, Events kept vs dropped-private /
dropped-duplicate / dropped-empty, correspondents count, proposals produced, any transcript-gaps,
and the commit SHA on the review branch (or `no-op`). Plain-English; no raw content.

## Composes these skills / modules

- The shared **ingest spine** — `ingest.pipeline.ingest_records` (sanitize → dedupe → normalize),
  `ingest.normalize.RawRecord`, and the **same email Sent/correspondents contract** as
  `ingest/adapters/email_source.py`. The egress / sanitize gate (`mcs_egress`) screens every item.
- The **genesis refresh** (`run_genesis` → `ReviewPacket`, proposals-only).
- `mcs:<project>:atomic-decompose` (sanitized-layer, proposals-only) for the new substance.
- Its proposals are gated by `mcs:<project>:morning` (the founder is the only ratifier) and its
  branch is consolidated by `mcs:<project>:close` / the next `morning` (abort-on-conflict, never
  auto-deleted).

## Owner routing (base-roster template — genesis proposes the rest)
Proposals this routine raises route to the owning agent. Ship with the **base roster** only —
**Coordinator / chief-of-staff** (orchestration / cross-cutting; the default ONLY for genuinely
cross-cutting work) · **Specialist A** (legal/business) · **Specialist B** (product) ·
**Specialist C** (software/build) · **operator** (the founder) — and let the genesis roster
proposer (the `≥ 3 distinct anchored signals` floor) add the company-specific specialists from
real evidence. Never re-propose a base-roster slot; never invent a role from thin air.

## Acceptance (a dry-read of this prompt must show)
It **reads** the attached email / Drive / calendar connectors **read-only** (Sent mailbox
included); **maps** each item into the normalized ingest record; runs the **shared ingest spine**
(`ingest.cloud.refresh` → `ingest_records`: sanitize → dedupe → normalize) + a **genesis refresh**;
**writes the correspondents file**; emits **proposals only** (each anchored, `status: proposed`)
for the morning gate; **commits to a review branch**, never the founder's main line; **forbids all
external sends / mutations** on every connector; keeps **raw private bodies out of git**; and is a
**clean no-op** (no Events, no rewrite, no commit) when idle.

## Rails (restated, the short list)
- Read-only on every connector; no sends, no mutations, no money, no credentials.
- No raw private bodies / secrets / PII / one-time codes in any committed file, log, or output —
  sanitize on the way in, fail-closed.
- Proposals only; ratification is the founder's at `morning`. Write to a review branch, never the
  founder's main line — and state plainly that a push-gate on a shared branch is unenforceable.
- Idle = inert (no empty commits). Everything reversible. The four hard limits (root `CLAUDE.md`
  §4) apply unchanged.
