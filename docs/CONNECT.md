# CONNECT — point the on-ramp at your own company's data

*Type-2 (FOR-THE-OPERATOR): plain English, opens "In plain terms," every technical term
explained the first time it appears. The companion to [SETUP.md](SETUP.md) (stand the system
up) and [SYSTEM.md](SYSTEM.md) (what it's made of). Honest about what auto-connects today vs.
what you do by hand — cross-checked against the live `ingest/` and `run.py` code.*

---

## In plain terms

**What this page is for.** [SETUP.md](SETUP.md) gets you running on a bundled fictional company.
This page is the next step: pointing the same on-ramp at **your** company's real data and
watching it draft *your* brain — who your people are, what you're working on, and a roster of AI
specialists — for you to approve.

> **Two ways your data is reached — read [INGEST-ARCHITECTURE.md](INGEST-ARCHITECTURE.md) first if
> you want your phone messages in.** Email, files, and calendar can be read by a scheduled program
> in the cloud. Your personal **iMessage and WhatsApp** cannot — they live in private databases on
> *your own Mac* and are read by a small **local agent** (`python -m ingest.local.sync`) that keeps
> them on your machine and emits only sanitized, you-approved notes. The split, the one-time macOS
> permission it needs, and how to set up each side are all in
> [INGEST-ARCHITECTURE.md](INGEST-ARCHITECTURE.md). The rest of *this* page covers the
> export-a-folder path (notes + email) you can run right now with no setup.

**The honest shape of "connect" today.** There is **no "Connect Gmail" button yet.** A real
connector (a piece of code that logs into Gmail/Slack/Drive and pulls your data automatically)
is the part still to build. So today you "connect" by **exporting** the sources you care about
into a folder and handing that folder to the on-ramp. That is less magical than a one-click
OAuth flow ("OAuth" = the standard "Sign in with Google / grant access" handshake), but it has a
real upside: **nothing ever logs into your accounts, and no credentials are stored anywhere.**
You stay in control of exactly what goes in.

**What you can run, right now, on your own data — one command:**

```sh
python3 run.py --sources /path/to/your/exported_notes_and_mail
```

That walks the full on-ramp on **your** files: it cleans them (stripping any secrets), turns
them into dated facts, resolves conflicts, drafts your knowledge pillars, proposes your vision
and an agent roster, prints a plain-English review packet, and — for each agent you approve —
builds a cited starter wiki. **Nothing is sent, nothing is applied, nothing touches your
accounts.** Every output is a draft under `genesis/out/` for you to read.

**The three things you must know before you point it at real data:**

1. **It only reads notes and email today** (`.md`/`.txt` notes and `.eml` mail). Chat, calendar,
   files, and code are designed but not wired in this repo — see [the source table](#what-each-source-looks-like-today).
2. **A privacy gate strips secrets *before* anything is read.** A stray API key, password, or
   Social-Security number in a note is dropped and never reaches the model or a wiki. You'll see
   the count as `dropped private` in the run summary. (This is enforced in code, not a promise —
   see [The privacy gate](#the-privacy-gate-what-gets-dropped-and-why).)
3. **By default a small *offline* model does the thinking** — a deterministic stand-in so you
   can run with no API key and no internet. To use a real model (e.g. a hosted LLM), you inject
   one yourself, and every call to it is forced through the same privacy gate. See
   [Using a real model](#using-a-real-model-optional).

**What you must decide (only you can):** which real model the engine calls, and — before this
repo goes public at all — the license question (see [LICENSE-NOTE.md](LICENSE-NOTE.md)). Neither
is decided for you.

---

## Step 1 — Get your data into a folder

The on-ramp reads a **source folder**. Two layouts work:

- **Recommended:** a folder with two subfolders —
  - `notes/` — your `.md` and `.txt` files (founder notes, decision logs, standups, planning
    docs).
  - `mail/` — your `.eml` files (individual exported email messages; `.eml` is the standard
    single-message email file most clients can export).
- **Or:** a single flat folder containing those file types mixed together. The on-ramp falls
  back to reading both notes and mail out of the one folder. Each reader only picks up the file
  types it understands, so the mix is safe.

```
your-company-export/
├── notes/
│   ├── 2026-05-02-founding-decision.md
│   ├── 2026-06-01-standup.md
│   └── early-plan.txt
└── mail/
    ├── supplier-thread.eml
    └── support-escalation.eml
```

> **You do not need to clean or organize anything first.** Duplicates are de-duplicated, secrets
> are stripped, and empty files are skipped automatically. Dump in what you have.

### How to export, by source

| Source | How to get it into the folder today |
|---|---|
| **Email** | From Gmail/Outlook/Apple Mail, select messages and **export/save as `.eml`** (Apple Mail: drag messages to a Finder folder; Gmail: "Show original" → download, or use Google Takeout and split the mbox). Drop the `.eml` files into `mail/`. |
| **Notes / docs** | Anything already in Markdown or plain text goes straight into `notes/`. Export Google Docs / Notion / Word as Markdown or `.txt` first. |
| **iMessage / WhatsApp** | **Read locally — see [INGEST-ARCHITECTURE.md](INGEST-ARCHITECTURE.md).** Not via this folder: a local Mac agent (`python -m ingest.local.sync`) reads them on your machine, allowlisted + sanitized. Needs a one-time macOS Full Disk Access grant. |
| **Chat (Slack, etc.)** | **Not read yet.** If you have a Slack export, the per-message text won't be ingested until the chat adapter is built. For now, paste the important threads into a `.md` note. |
| **Calendar** | **Not read by this folder** — it's the **cloud routine's** job. Stand the routine up (turnkey recipe + paste-in prompt: [CLOUD-ROUTINE.md](CLOUD-ROUTINE.md); adapt the [`examples/cloud-routine/`](../examples/cloud-routine/) templates) and it reads your calendar read-only. For a one-off, paste a meeting's notes/agenda into a `.md` note. |
| **Files (Drive, etc.)** | **Not read by this folder** — also the **cloud routine's** job ([CLOUD-ROUTINE.md](CLOUD-ROUTINE.md)). For a one-off, export the documents that matter to Markdown/`.txt` and put them in `notes/`. |
| **Code host (GitHub, etc.)** | **Not read yet** as a live connector. |

This is the manual stand-in for the auto-pull connectors that are the next build. The point of
v1 is that the **engine** works on real data — you feed it by hand for now.

---

## Step 2 — Run the on-ramp on your folder

```sh
# From the repo root.
python3 run.py --sources /path/to/your-company-export

# Non-interactive variants (handy for a first dry look):
python3 run.py --sources /path/to/your-company-export --auto-ratify=none  # approve nothing
python3 run.py --sources /path/to/your-company-export --auto-ratify       # approve everything
```

> If `python3` on your machine is a newer Homebrew build whose XML parser (`pyexpat`) is broken,
> the **tests** may fail to start — use `/usr/bin/python3` (the system Python) instead. The
> on-ramp itself is pure standard library and runs on any Python 3.9+.

You'll see seven stages print in order:

1. **CONNECT** — which folders it's reading.
2. **INGEST** — `kept` / `dropped private` / `dropped dup` / `dropped empty` counts. **Watch
   `dropped private`** — that's the privacy gate at work.
3. **GENESIS** — how many knowledge pillars filled and how many proposals it produced (all
   marked `proposed`).
4. **REVIEW** — the **"In plain terms"** packet: what it understood per pillar, the agents it
   proposes (each with a one-line why and an evidence count), and the meta-initiatives
   (24-month thrusts) it derived.
5. **RATIFY** — your gate. Interactively it asks, per proposed agent, `ratify this agent?
   [y]es / [n]o`. Answer `y` only for the specialists you actually want. (Empty/Enter = no —
   it fails closed, so you never stand up an agent you didn't clearly approve.)
6. **BUILD** — for each agent you ratified, it writes a **cited DRAFT wiki** under
   `genesis/out/` (every fact in it points back to the source it came from).
7. **HANDOFF** — where to go next: the steering skills in `skills/` and the self-improvement
   loop in `loop/`.

**Everything it writes is a draft under `genesis/out/`.** Nothing is applied to a live brain,
nothing is sent anywhere, and no git command runs. Read the drafts, and re-run as many times as
you like.

---

## The privacy gate — what gets dropped, and why

This is the safety component that makes it OK to point the tool at real company data.

**What it does:** before any of your records becomes a fact the engine can read, each one is
classified. If it carries a **secret** (an API key, an access token, a password), a
**credential**, or **personal identifying information** (e.g. a Social-Security/national-ID
number), the **whole record is dropped** — it never becomes an event, never reaches the model,
and never appears in a wiki. Anything the classifier *can't confidently call safe* is treated as
private and dropped too (this is called **fail-closed**: when in doubt, keep it out).

**Why it matters in real-world terms:** your notes and inbox are exactly where stray secrets
live — a password pasted into a note, a token in an email. Without this gate, those could end up
copied into your brain or, worse, sent to an outside model. The gate means **a secret in your
data stays in your data.**

**How you'll see it:** the `dropped private` count in the INGEST stage. If you expected 50
records and see `kept: 48, dropped private: 2`, two records were withheld for carrying something
sensitive — by design.

> This gate is the same `EgressGate` that guards every call to the model (it's enforced in
> `genesis/genesis_contracts.py` and the ingest spine in `ingest/`, and covered by the test
> suite). It is a hard rule of the system, not a setting you can quietly turn off.

---

## Using a real model (optional)

By default the on-ramp uses a small **offline model** — a deterministic stand-in (`a fixed,
scripted reasoner`) so you can run with **no API key and no internet**. It reads the real
evidence in your data and proposes grounded items, but it is not as smart as a hosted model. The
engine's safety floors (every proposal must cite real evidence or it's dropped; an agent needs
≥3 distinct supporting sources before it's proposed) apply the same way no matter which model
you use.

To use a real model, **inject one** — any object with this one method:

```python
def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
    ...  # call your model of choice, return its text
```

The contract (the `LLM` protocol) lives in
[`genesis/genesis_contracts.py`](../genesis/genesis_contracts.py); a worked example you can copy
is the `OfflineGenesisLLM` class at the top of [`run.py`](../run.py). You pass your model into
`run_journey(..., llm=your_model)` (call the function directly from your own Python — the bare
`python3 run.py` command line always uses the built-in offline model).

**The boundary that always holds:** every prompt sent to *any* model — offline or hosted — is
forced through `EgressGate.guard()` first. So even with a real third-party model wired in, **raw
private company content cannot leave to it.** This is why the privacy gate is not optional: it's
the thing that lets you safely connect a real model to a real private corpus.

> **Cost and account note:** a hosted model means an account and an API key with that provider.
> Place any such key in an environment variable or a git-ignored config — **never in a committed
> file** (the repo's `.gitignore` already lists token/credential patterns). Choosing the model
> and standing up that account is a decision only you can make.

---

## What each source looks like today (build state)

So you're not surprised hunting for a connector that isn't there:

| Source | Auto-pull connector | What you do today |
|---|---|---|
| **Email** (`.eml`) | **Stand up from a recipe** — the scheduled **cloud routine** ([CLOUD-ROUTINE.md](CLOUD-ROUTINE.md) + [`examples/cloud-routine/`](../examples/cloud-routine/) templates); the email *adapter* it reuses is built + tested | Export to `.eml`, drop in `mail/` — **reads today**; or stand up the routine for hands-off email. |
| **Notes / docs** (`.md`/`.txt`) | n/a (just files) | Drop in `notes/` — **reads today.** |
| **iMessage / WhatsApp** | **Built — the local Mac agent** (`ingest/local/`, `python -m ingest.local.sync`) | Grant Full Disk Access + a correspondents list; runs locally. See [INGEST-ARCHITECTURE.md](INGEST-ARCHITECTURE.md). |
| **Chat** (Slack) | Not built (Slack was retired upstream; needs a full rebuild) | Paste key threads into a `.md` note. |
| **Calendar** | **Stand up from a recipe** — the cloud routine ([CLOUD-ROUTINE.md](CLOUD-ROUTINE.md) + [`examples/cloud-routine/`](../examples/cloud-routine/)) | Stand up the routine, or paste meeting notes into a `.md` note. |
| **Files** (Drive) | **Stand up from a recipe** — the cloud routine ([CLOUD-ROUTINE.md](CLOUD-ROUTINE.md) + [`examples/cloud-routine/`](../examples/cloud-routine/)) | Stand up the routine, or export the docs that matter to `.md`/`.txt`. |
| **Code host** (GitHub) | Not built | — (feeds the R&D pillar once built). |

The reusable **clean-up spine** the connectors will feed — sanitize → normalize → dedup — is
already here and tested (`ingest/`). Building a connector is "fetch from the source and hand
records to the spine," not "reinvent the pipeline."

---

## Troubleshooting

- **`ERROR: --sources is not a directory`** — the path you passed doesn't exist or isn't a
  folder. Check it.
- **`No usable events`** — the folder had no `.md`/`.txt`/`.eml` the readers understood (or
  everything was empty/duplicate/dropped-private). Point `--sources` at a folder that has those
  file types.
- **`pytest` won't start (a `pyexpat` error)** — a broken Python build, not your data. Use
  `/usr/bin/python3`. The on-ramp itself doesn't need pytest.
- **No agents proposed** — that's not a bug. An agent is only proposed when ≥3 *distinct*
  sources point at the same recurring domain. A small or single-thread corpus correctly yields
  none. Add more of your real history and re-run.
- **A record you expected is missing** — check the `dropped private` count; it may have carried
  something the privacy gate classified as sensitive (by design).

---

## Where to go next

- **Get your phone messages in (the local↔cloud split):** [INGEST-ARCHITECTURE.md](INGEST-ARCHITECTURE.md).
- **Stand up the hands-off cloud half (email/Drive/calendar) — turnkey recipe + paste-in prompt:**
  [CLOUD-ROUTINE.md](CLOUD-ROUTINE.md), with the adaptable [`examples/cloud-routine/`](../examples/cloud-routine/) templates.
- **Stand the whole system up + the operator decisions:** [SETUP.md](SETUP.md).
- **What the machine is made of (every feature/agent/skill/tool + build state):**
  [SYSTEM.md](SYSTEM.md).
- **The rules every clone inherits (how to think/build/talk, the hard limits):**
  [CLAUDE.md](../CLAUDE.md).
- **After your brain is seeded — steer it:** the skills in [`skills/`](../skills/) (the daily
  `morning` gate, `ramble`/`vision`/`manifest`, `pulse`/`close`) and the self-improvement loop
  in [`loop/`](../loop/).

---

*Built-vs-not status here was verified against the live `ingest/` adapters and `run.py` (the
`--sources` real-folder path runs; the privacy gate drops sensitive records; the offline model
is the default and a real model injects via the `LLM` protocol).*
