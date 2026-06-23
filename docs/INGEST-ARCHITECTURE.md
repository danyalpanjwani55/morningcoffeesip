# INGEST ARCHITECTURE — the local Mac agent vs. the cloud routine

*Type-2 (FOR-THE-OPERATOR): plain English, opens "In plain terms," every technical term
explained the first time it appears. The companion to [CONNECT.md](CONNECT.md) (point the
on-ramp at your data), [SETUP.md](SETUP.md) (stand the system up), and [SYSTEM.md](SYSTEM.md)
(what the machine is made of). Cross-checked against the live `ingest/local/` code.*

---

## In plain terms

**What this page answers.** Your company's history lives in two very different kinds of place,
and they have to be reached in two different ways. This page explains the split, why it exists,
and how to set up each side — without sending anything anywhere it shouldn't go.

**The split, in one sentence.** Almost everything (your email, your shared files, your calendar)
can be read by a program **in the cloud** that you point at this project — but **your personal
text messages (iMessage and WhatsApp) cannot.** Those two live in private databases on *your own
Mac*, locked behind a macOS permission, with no internet service to ask. So they need a small
program that runs **on your machine** — the **local Mac sync agent** — and everything else runs
as a scheduled **cloud routine**.

**Why it's built this way (the real-world reason).** A cloud program has no way to reach into
your laptop and read your Messages history — Apple deliberately locks that file away. Trying to
force it would mean shipping your private messages off your machine, which is exactly what we
refuse to do. So the rule is: **the personal stuff is read locally and never leaves your Mac
except as cleaned-up, you-approved notes; everything else, which is already reachable by normal
account access, runs in the cloud.**

**The boundary that always holds.** The local agent reads your raw messages **only on your Mac**.
What leaves that machine is never the raw messages — it's a **sanitized "Event"** (a single dated
note like "discussed the launch date with a teammate"), with secrets stripped, only for the
people you've said you correspond with, and **nothing is ever sent** on your behalf. You stay in
control of what becomes part of the brain.

**What you must decide (only you can):**
1. **Who counts as "someone you correspond with."** The system only ingests messages from people
   on that list (see [The allowlist](#the-allowlist--who-counts-as-someone-you-correspond-with)).
   No list = nothing ingested. This is opt-in by design.
2. **Whether to grant the local agent Full Disk Access** — a one-time macOS permission, described
   below. Without it, the local lanes simply read nothing (they don't break — they skip).

---

## The two halves at a glance

| | **LOCAL — the Mac sync agent** | **CLOUD — the Claude routine** |
|---|---|---|
| **Sources** | iMessage, WhatsApp | Email, Drive/files, Calendar |
| **Why this side** | The data is in private SQLite files on your Mac; **no cloud API exists** to fetch it | Reachable through normal account access; **no local code needed** |
| **Where it runs** | On your own machine | On a scheduled cloud agent pointed at this repo |
| **Needs** | macOS **Full Disk Access** (a manual grant) | The connectors attached to the routine (read-only) |
| **What leaves the machine** | Only **sanitized Events** you control — never raw messages | Derived notes only — never raw secret content |
| **Entry point** | `python -m ingest.local.sync` | a scheduled Claude routine (see below) |

A term, once: an **API** ("application programming interface") is a service a program can call
over the internet to fetch data. Email/Drive/Calendar have one; your personal iMessage and
WhatsApp history do not — which is the whole reason for the split.

---

## The LOCAL side — the Mac sync agent

### What it is

A small program you run on your Mac: `python -m ingest.local.sync`. It reads the two on-device
message stores, keeps only the conversations with people you correspond with, strips anything
sensitive, and writes the result as dated **Events** into your **local brain store** — a plain
text file on your machine. It is **proposals-only**: it never sends a message, never posts
anything, never touches the internet, never runs git, never moves money. Think of it as a
note-taker that reads your messages on your behalf and writes you clean summaries.

### Where the data lives (and why a manual grant is unavoidable)

The two stores it reads are real files on macOS:

- **iMessage** — `~/Library/Messages/chat.db`
- **WhatsApp** — `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite`
  (plus `ContactsV2.sqlite` alongside it, used only to match a phone number to a name)

These files are **SQLite databases** (SQLite = a small self-contained database stored as a single
file). macOS keeps them behind a privacy wall called **Full Disk Access**: a program can only read
them if *you* tick a box for it in **System Settings → Privacy & Security → Full Disk Access**.

> **This grant cannot be automated.** It is a deliberate, manual, one-time action you take in
> System Settings — by design, no script can grant it for you (that's the point of the wall). The
> honest consequence: until you grant it, the local lanes read **nothing** — and that's fine, they
> skip cleanly rather than erroring. The cloud side keeps working regardless.

The agent opens both files **strictly read-only** — it physically cannot change your Messages or
WhatsApp data. (Because the real files need that manual grant, the code is built and tested
against *synthetic* stand-in databases that copy the real layout — so the logic is proven without
ever needing your private data; see [How this is tested honestly](#how-this-is-tested-honestly).)

### The allowlist — who counts as "someone you correspond with"

This is the gate that makes it safe to point the agent at your personal messages. The rule the
whole system turns on: **a message is only ingested if one of the people in it is someone you
already correspond with.** That list of people is derived from your **sent email** (the addresses
you've actually emailed) — the cloud email routine produces it; the local agent consumes it.

- A conversation with someone **not** on the list is dropped **before** its contents are even
  looked at.
- **No list = nothing ingested.** This is "fail-closed": when in doubt, keep it out. You opt in by
  declaring who you correspond with — there is deliberately **no "ingest everything" switch**,
  because that would defeat the whole boundary.
- On a Mac, the system also matches the *phone numbers and handles* of those people automatically
  (by reading your Contacts read-only), so a person you email at work is recognized when they text
  you from their phone.

In practice you give the agent a small **correspondents file** — a plain list of the email
addresses you correspond with (one per line). The default location is
`<your brain>/sources/sent-correspondents.txt`, and the email cloud routine can write it for you.

### Keeping the allowlist current — the refresh cadence

**This is not a set-and-forget list, and forgetting it loses data silently.** The allowlist is only
as current as the last time the correspondents file was written. The failure mode is quiet: you email
20 new people next month, none of them land on a file you wrote on day one, so **none of their
messages get ingested** — no error, just silence.

The fix is to **rebuild the correspondents file on a cadence**, and the cleanest way is to let the
**cloud routine do it for you**:

- **If you run the cloud routine** ([CLOUD-ROUTINE.md](CLOUD-ROUTINE.md)), it re-harvests your SENT
  mail and **rewrites `sent-correspondents.txt` on every run.** So as long as that routine is actually
  *scheduled* (hourly/daily), the list stays current automatically and newly-emailed people are
  recognized within one run. Scheduling the routine **is** the refresh mechanism.
- **If you write the file by hand** (e.g. local-only on a Mac, no cloud routine), then refreshing is
  **on you**: re-export it whenever you start corresponding with new people you also message. A
  monthly habit is a sane floor.
- **Either way, the local agent re-reads the file fresh every run** — no caching. The moment the file
  is updated, the next `python -m ingest.local.sync` picks up the new people.

### The headless / non-Mac fallback — supplying Contacts by hand

The phone↔email matching described above (recognizing a person you email when they text) works by
reading your **macOS Contacts** read-only. On a **non-Mac or headless clone** there is no Contacts
store to read — so by default matching is **email-only**: a correspondent is matched in messages only
by an address that actually appeared in your email, not by a phone number that never did.

This is a graceful, fail-open degradation, **not a break** — the local lanes still run, just with the
narrower email-only allowlist. (And note: WhatsApp/iMessage history itself lives in macOS SQLite
files, so a truly non-Mac clone typically has no local message stores to read anyway; this fallback
matters most for a headless macOS setup, or a clone that has copied those stores elsewhere.)

**If you do need phone/handle matching without a readable Contacts store, supply the mapping
yourself** — the seam already exists in code. `ingest.allowlist.build_allowlist` takes an optional
`contacts` argument: a map from each correspondent email to the other identities (phone numbers,
chat handles) that same person reaches you by. Passing it skips the Contacts read entirely and uses
your map instead:

```python
from ingest.allowlist import build_allowlist

# Email-only (no Contacts read at all) — pass an explicit empty map:
allowlist = build_allowlist(sent_correspondents, contacts={})

# Email + a hand-supplied phone/handle map (the headless equivalent of Contacts):
allowlist = build_allowlist(
    sent_correspondents,
    contacts={
        "teammate@example.com": ["+15555550123", "their_chat_handle"],
    },
)
```

(Leaving `contacts=None`, the default the `python -m ingest.local.sync` CLI uses, is what triggers the
macOS Contacts read — and fails open to email-only when no store is present. The explicit-map form
above is the documented way a non-Mac clone gets the same matching the Mac gets for free.) The
contract is in [`ingest/allowlist.py`](../ingest/allowlist.py) (`build_allowlist`).

### How to set it up

1. **Grant Full Disk Access** to the program that will run the agent (your Terminal app, or
   whatever runs Python), in **System Settings → Privacy & Security → Full Disk Access**. One time.
2. **Provide the correspondents list** — let the email cloud routine write
   `sources/sent-correspondents.txt`, or create it yourself (one email address per line; lines
   starting with `#` are ignored).
3. **Run it** (start with a dry run, which writes nothing):

   ```sh
   # See exactly what WOULD be ingested — writes nothing:
   python -m ingest.local.sync --dry-run

   # Do it for real (writes sanitized Events to your local brain store):
   python -m ingest.local.sync

   # Narrow it down while you're getting comfortable:
   python -m ingest.local.sync --lane imessage
   python -m ingest.local.sync --correspondents /path/to/your-list.txt
   ```

   You'll see a per-lane summary: how many messages were read, how many were admitted by the
   allowlist, how many Events were kept, and how many were dropped for carrying a secret. Run it
   on a clock (e.g. a scheduled job on your Mac) to keep the brain current.

**What it writes, and where.** A single append-only file, `<your brain>/local/events.jsonl`
(JSONL = one JSON record per line — easy to read back and add to). Re-running is **idempotent**
(a fancy word for "running it twice doesn't duplicate anything" — it skips Events already there).

---

## The CLOUD side — the Claude routine

> **The full recipe is its own page: [CLOUD-ROUTINE.md](CLOUD-ROUTINE.md).** It has the step-by-step
> job description, the exact rails, and a **paste-in prompt** to stand the routine up. This section is
> the summary; that page is what you actually follow.

### What it is

For email, Drive/files, and calendar there is **no local code at all.** These run as a scheduled
**Claude routine** — a cloud agent that runs on a clock, with read-only access to those accounts
attached to it, pointed at a fresh copy of this repository. It reads what's new, drafts updates to
the brain, and is a clean no-op when nothing changed. (This mirrors the upstream "brain-refresh"
cloud routine the design is modeled on.) The routine is also what **writes the correspondents file**
the local lanes depend on — so it is the first domino: run it once before the local agent, or the
local lanes correctly ingest nothing.

### Its rails (the same boundary, from the other side)

- **Read-only on every connector.** It may read email/Drive/calendar; it must **never** send an
  email, change a calendar, move a file, move money, or touch a credential.
- **No raw secrets leave.** It writes only *derived* notes — never a raw private body, a password,
  or a one-time code — into what it commits.
- **Idle = nothing.** If nothing changed since last run, it does nothing at all.

### How to set it up

**Follow [CLOUD-ROUTINE.md](CLOUD-ROUTINE.md)** for the full recipe + paste-in prompt. In short:

1. Create a scheduled Claude routine pointed at this repository.
2. Attach the **read-only** connectors it needs (email, Drive, calendar).
3. Give it the job description (the paste-in prompt in [CLOUD-ROUTINE.md](CLOUD-ROUTINE.md)).
4. Schedule it (e.g. hourly). It keeps the email/Drive/calendar side of the brain current without
   your computer being open — the exact complement to the local agent, which must run on your Mac —
   and rewrites the correspondents file each run, which is also how the allowlist stays fresh.

---

## The data-boundary posture (the rule both halves obey)

One rule, stated plainly, governs both sides: **raw private content never leaves the machine it
lives on except as a sanitized, you-approved Event.**

- **On the way in (sanitize).** Every message body is screened. If it carries a **secret** (an API
  key, a token, a password), a **credential**, or **personal identifying information** (e.g. a
  Social-Security number), the whole record is **dropped** — it never becomes an Event. Anything
  the screener can't confidently call safe is dropped too (fail-closed).
- **On the way out (classify).** Any text headed to an outside model is checked by the same gate
  first; private content cannot leave to it.
- **Allowlist first.** Off-the-list conversations are dropped before their contents are read at
  all.
- **Proposals-only.** Nothing either half produces is ever *sent* or *applied* on its own. It's a
  proposal; you (and the morning review) decide what becomes brain truth.

This is enforced in code (the shared ingest spine and the egress gate), not by good intentions —
and it's covered by the test suite.

---

## How this is tested honestly

The real `chat.db` and WhatsApp store need Full Disk Access — a manual grant no test can perform.
So rather than pretend, the code is exercised against **synthetic databases**: tiny SQLite files
built in each test to copy the *real layout* of those stores (the same tables and columns the
adapters actually read). This proves the parsing, the allowlist, the secret-stripping, and the
store-write logic work correctly — **without ever touching your private data or needing the
grant.** The manual grant is documented here as the one honest, un-automatable step.

The headline guarantee the tests lock in: running the local agent with `--dry-run` over an
**absent or empty** store does **nothing at all** — no Events, no file written — and the same run
with no correspondents list ingests nothing. Safe to run before you've set anything up.

---

## Where to go next

- **Stand up the cloud half (the full recipe + paste-in prompt):** [CLOUD-ROUTINE.md](CLOUD-ROUTINE.md).
- **Point the on-ramp at your data:** [CONNECT.md](CONNECT.md).
- **Stand the whole system up:** [SETUP.md](SETUP.md).
- **What the machine is made of:** [SYSTEM.md](SYSTEM.md).
- **The rules every clone inherits (how to think/build/talk, the hard limits):** [CLAUDE.md](../CLAUDE.md).

---

*Built-vs-not status here was verified against the live `ingest/local/` code: the local sync
entrypoint (`ingest/local/sync.py`) runs the iMessage + WhatsApp adapters, correspondent-allowlists
them, sanitizes via the shared spine, and writes proposals-only Events to a local JSONL store;
`--dry-run` and absent/empty stores are a clean no-op. The cloud routine for email/Drive/calendar
is a scheduled Claude routine (no local code), modeled on the upstream brain-refresh routine.*
