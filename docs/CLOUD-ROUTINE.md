# CLOUD ROUTINE — the email / Drive / calendar half, set up for real

*Type-2 (FOR-THE-OPERATOR): plain English, opens "In plain terms," every technical term
explained the first time it appears. The cloud companion to
[INGEST-ARCHITECTURE.md](INGEST-ARCHITECTURE.md) (the local↔cloud split), [CONNECT.md](CONNECT.md)
(point the on-ramp at your data), and [SETUP.md](SETUP.md) (stand the system up). This page is the
concrete recipe the architecture page points at — what the cloud half actually **does**, the rails
it must obey, and a paste-in prompt to stand it up.*

---

## In plain terms

**What this page is.** [INGEST-ARCHITECTURE.md](INGEST-ARCHITECTURE.md) explains *why* your data is
read in two halves: the personal stuff (iMessage, WhatsApp) by a small program on your Mac, and
everything else (email, shared files, calendar) by a program **in the cloud**. That page tells you
the local half is real, runnable code. This page is the missing other half: **exactly what the
cloud program does, the guardrails it runs under, and a recipe you can paste in to create it.**

**The honest status, first.** The cloud half is **not pre-built runnable code in this repo** — it
is a **scheduled agent you stand up**, pointed at a fresh copy of this repository, following the
recipe below. (A term, once: a **scheduled agent**, here called a **routine**, is an AI assistant
set to run on a clock — e.g. once an hour — with read-only access to the accounts you attach to it.
It is the cloud equivalent of a cron job, but for an assistant rather than a script.) Why a recipe
and not shipped code: the local half talks to fixed files on one machine, so it can be plain Python;
the cloud half talks to *your* email/Drive/calendar through whatever connectors your assistant
platform gives you, which differ per platform and per account — so the portable, clone-safe thing to
ship is the **specification of the job**, not a binary welded to one provider.

**What the cloud routine does, in one sentence.** On each scheduled run it reads what's *new* in
your email, shared files, and calendar; turns it into the same kind of dated, sanitized note
("Event") the rest of the system uses; and — crucially — writes the **correspondents file** that the
local message lanes depend on (the list of people you actually email). It **never sends, replies,
changes a calendar, moves a file, or stores a password.**

**Why it matters that this exists (the real-world consequence).** Without a written-down recipe, the
cloud half is a sentence — "set up a routine" — and a stranger cloning this repo has no idea what the
routine should *do*, what it must *never* do, or how it connects to the local half. Worse: the local
iMessage/WhatsApp lanes **ingest nothing until this routine has run at least once**, because the
local lanes only admit messages from people on the correspondents list, and **this routine is the
thing that writes that list.** So this page isn't optional polish — it's the first domino. Set up the
cloud routine, let it run once, *then* the local agent has someone to listen to.

**What you must decide (only you can):**
1. **Which assistant platform runs the routine**, and **which read-only connectors you attach** to
   it (email, Drive, calendar). This repo can't pick or pay for that.
2. **The schedule** (hourly is fine; daily is fine). More often = fresher brain, more runs.
3. **Whether to let the routine write the correspondents file automatically**, or to review it first
   (see [The correspondents file](#the-correspondents-file--the-handoff-to-the-local-half)). It is
   the gate that scopes your personal messages, so some founders prefer to eyeball it once.

---

## What the routine does, step by step

Each scheduled run performs exactly these steps, in order. (This is the contract the paste-in prompt
below encodes — read it as "the job description.")

1. **Read what's new — read-only.** Look at email, shared files, and calendar entries that are newer
   than the last run. It may **read** them; it may **never** send, reply, change, move, or delete
   anything. If a connector isn't attached, that source is simply skipped — not an error.

2. **Harvest your correspondents from SENT mail.** From the messages **you have sent**, collect every
   address you wrote **To** or **Cc**. That set — the people you actually email — is the
   *correspondents list*. (This mirrors exactly what the local email adapter does in code; see
   [`ingest/adapters/email_source.py`](../ingest/adapters/email_source.py).)

3. **Drop inbound spam by the same rule.** For inbound email, keep a message **only if its sender is
   someone you've emailed** (i.e. is in the correspondents set). Newsletters, cold outreach, and spam
   — senders you never wrote to — are dropped before their contents are used. This is the same
   "two-way relationships only" filter the whole system turns on.

4. **Sanitize on the way in.** Every surviving item is screened for **secrets** (an API key, an access
   token, a password), **credentials**, or **personal identifying information** (e.g. a national-ID
   number). Anything carrying one is **dropped whole** — it never becomes an Event. Anything that
   can't be confidently called safe is dropped too (**fail-closed**: when in doubt, keep it out).

5. **Write derived notes only — never raw bodies.** What the routine commits into the brain is a
   **sanitized Event** (a short dated note like "discussed the supplier timeline with a teammate"),
   never the raw email body, never a calendar invite verbatim, never a file's contents. Same boundary
   as the local half, from the other side.

6. **Write the correspondents file** (the handoff to the local half — see
   [below](#the-correspondents-file--the-handoff-to-the-local-half)).

7. **Idle = nothing.** If nothing changed since the last run, the routine does nothing at all — no
   empty commits, no noise. A clean no-op is the correct outcome on a quiet hour.

8. **Propose, never apply.** Everything the routine produces is a **proposal** for the morning review
   (`/morning`), exactly like the local agent's output. The routine never decides what becomes brain
   truth — you do.

---

## The rails (the same boundary, enforced from the cloud side)

These are not suggestions; they are the hard limits from [CLAUDE.md](../CLAUDE.md) §4, restated for
this specific job. The paste-in prompt states them to the routine explicitly so it operates inside
them.

- **Read-only on every connector.** Read email / Drive / calendar; **never** send an email, reply,
  change or create a calendar event, move or delete a file, move money, or touch a credential.
- **No raw secrets ever leave.** Commit only *derived* notes — never a raw private body, a password,
  an access token, or a one-time code.
- **No external sends, full stop.** The routine has no business sending anything to anyone. If it ever
  appears about to, that is a bug to stop on, not a step to take. (This is the exact failure the
  `/morning` "routine-send detector" exists to catch — see [SYSTEM.md](SYSTEM.md) feature 8.)
- **Proposals-only.** Writes drafts for the morning gate; never applies to a live brain on its own.
- **Idle = inert.** Nothing new → nothing done.

> **A note on the "don't apply" gate (be honest about what holds).** A routine running in the cloud,
> committing to a shared branch, **cannot be technically prevented** from pushing by a rule in a file
> — the same `trust-git-not-self-report` truth from the doctrine. So the safe posture is: point the
> routine at a **separate branch** (not your `main`), and treat everything it produces as a proposal
> that **you** merge at the morning gate. Don't rely on the routine's good intentions to hold a
> push-gate it could cross; rely on it writing to a branch you review.

---

## The correspondents file — the handoff to the local half

This is the single most important thing the cloud routine produces, because it is what makes the
**local** iMessage/WhatsApp lanes work at all.

**What it is.** A plain text file — one email address per line — listing the people you correspond
with (the To/Cc of your sent mail, from step 2 above). Lines starting with `#` are comments.

**Where it goes.** The local agent looks for it, by default, at:

```
<your brain>/sources/sent-correspondents.txt
```

(`<your brain>` is your brain root — resolved by `mcs_paths`; see [SETUP.md](SETUP.md). You can also
point the local agent at a different path with `--correspondents`, or the `MCS_CORRESPONDENTS`
environment variable.)

**Why it's the first domino.** The local message lanes are **opt-in and fail-closed**: a message is
ingested only if one of its participants is on this list, and **an empty/absent list ingests
nothing.** So the order is always:

1. Cloud routine runs → writes `sent-correspondents.txt`.
2. *Then* the local agent (`python -m ingest.local.sync`) has a list to filter against, and your
   personal messages from those same people start flowing in (allowlisted + sanitized).

Run the cloud routine **before** the local agent the first time, or the local agent correctly does
nothing.

> **On a Mac, the list gets richer automatically.** When the local agent applies this list, it also
> reads your macOS Contacts (read-only) to match each emailed person's **phone numbers and chat
> handles** — so someone you email at work is recognized when they text you. On a non-Mac / headless
> clone there's no Contacts store to read, so matching stays **email-only** unless you supply the
> phone↔email mapping yourself — see *"the headless fallback"* in
> [INGEST-ARCHITECTURE.md](INGEST-ARCHITECTURE.md#the-headless--non-mac-fallback-supplying-contacts-by-hand).

---

## Keep it current — the refresh cadence (don't set-and-forget)

**The trap.** You email 30 new people over a month. None of them are on the correspondents file you
wrote on day one. So **none of their messages get ingested** — silently, with no error. The
allowlist is only as current as the last time the correspondents file was rebuilt.

**The fix — and why scheduling the cloud routine solves it for free.** The cloud routine re-harvests
your SENT mail on **every run** and rewrites `sent-correspondents.txt`. So as long as the routine is
**actually scheduled** (not run once by hand and forgotten), the list stays current automatically,
and newly-emailed people start being recognized within one run. Concretely:

- **Schedule the routine** (hourly or daily). That is the whole refresh mechanism — a fresh list each
  run.
- **If you are NOT running the cloud routine** (e.g. you only use the local half on a Mac, and write
  the correspondents file by hand), then **rebuilding the list is on you**: re-export it whenever you
  start emailing new people you also message. A monthly habit is a sane floor.
- **The local agent always reads the file fresh** at each run, so the moment the file is updated, the
  next local sync picks up the new people. No caching, no restart.

This same cadence note lives in
[INGEST-ARCHITECTURE.md](INGEST-ARCHITECTURE.md#keeping-the-allowlist-current--the-refresh-cadence);
it's repeated here because the cloud routine is the thing that *performs* the refresh.

---

## How to set it up

1. **Create a scheduled routine on your assistant platform**, pointed at a fresh copy of this
   repository, with **read-only** connectors attached for the sources you want (email, Drive,
   calendar). Email is the one that matters most — it produces the correspondents file.
2. **Give it the job description.** Paste the prompt below as the routine's standing instructions. It
   encodes the steps and rails above so the routine operates inside the boundary.
3. **Pick a schedule** (hourly is a good default).
4. **Let it run once, then run the local agent.** After the first cloud run has written
   `sources/sent-correspondents.txt`, run `python -m ingest.local.sync --dry-run` on your Mac to see
   the local lanes light up against the freshly-written list (dry-run writes nothing). See
   [INGEST-ARCHITECTURE.md](INGEST-ARCHITECTURE.md).

### The paste-in prompt (the routine's standing instructions)

> You are a scheduled, read-only ingest routine for a MorningCoffeeSip company brain. On each run:
>
> 1. Read what is **new since your last run** in the email, Drive/files, and calendar connectors
>    attached to you. **Read only.** Never send, reply to, change, create, move, or delete anything in
>    any connector. If a connector is not attached, skip that source silently.
> 2. From my **SENT** email, collect every address I wrote **To** or **Cc** — that is my
>    *correspondents list*. Exclude my own address.
> 3. For inbound email, keep a message **only if its sender is in that correspondents list**. Drop all
>    other inbound (newsletters, cold outreach, spam) before using its contents.
> 4. **Sanitize on the way in:** if any kept item contains a secret, credential, API key, access
>    token, password, one-time code, or personal-identifying number, **drop the whole item** — it must
>    never become a note. If you cannot confidently tell whether something is sensitive, **drop it**
>    (fail-closed).
> 5. Commit only **derived, dated notes** — short summaries — into the brain. **Never commit a raw
>    email body, a calendar invite verbatim, a file's contents, or any secret.**
> 6. Write/overwrite the file `sources/sent-correspondents.txt` under the brain root: one
>    correspondent email address per line (the list from step 2). This file is consumed by the local
>    message lanes.
> 7. If nothing changed since your last run, do **nothing** — no commits, no output.
> 8. Everything you produce is a **proposal** for the human's morning review. You **never** apply to a
>    live brain, **never** merge to the main branch, and **never** send anything to anyone. Write to a
>    review branch only.
>
> If you are ever about to send, reply, publish, or move money — **stop**; that is outside your job.

*(Adapt the file-path phrasing to however your platform references the repo's working tree. The
substance — read-only, correspondents-from-sent, sanitize-then-derive, proposals-only, write the
correspondents file — is the contract; keep all of it.)*

---

## How this is "tested honestly"

The local half is exercised against synthetic databases in code (see
[INGEST-ARCHITECTURE.md](INGEST-ARCHITECTURE.md#how-this-is-tested-honestly)). The cloud half is a
**job specification**, not shipped code, so it has no unit tests of its own — and this page says so
plainly rather than implying a green check it doesn't have. What *is* tested in code is the part the
routine reuses: the **correspondent-harvesting + spam-drop logic** (the email adapter,
[`ingest/adapters/email_source.py`](../ingest/adapters/email_source.py)) and the **sanitize / egress
gate** every Event passes through. The routine's job is to feed that proven spine from the cloud side
under the rails above — not to reinvent it.

---

## Where to go next

- **The local half + the local↔cloud split:** [INGEST-ARCHITECTURE.md](INGEST-ARCHITECTURE.md).
- **Point the on-ramp at your data (the export-a-folder path you can run now):** [CONNECT.md](CONNECT.md).
- **Stand the whole system up:** [SETUP.md](SETUP.md).
- **What the machine is made of:** [SYSTEM.md](SYSTEM.md).
- **The rules every clone inherits (how to think/build/talk, the hard limits):** [CLAUDE.md](../CLAUDE.md).
