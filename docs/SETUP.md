# SETUP — the MorningCoffeeSip on-ramp

*The step-by-step guide for a technical-ish founder cloning this repo to stand up their own
company brain. Type-2 (FOR-THE-OPERATOR): plain-English, opens "In plain terms," every
technical term explained the first time it appears. Honest about what's built vs not — every
stage is cross-checked against the ship-gating review
([SHIP-GATING-REVIEW.md](SHIP-GATING-REVIEW.md))
and the live files in `genesis/`.*

---

## In plain terms (read this first)

**What you're setting up.** MorningCoffeeSip is meant to read your company into existence. You
connect your data sources (email, chat, files, calendar, code), it ingests them, a **genesis
engine** ("genesis" = the cold-start pass that builds the brain from nothing) turns that raw
data into dated, sourced facts and proposes your company's vision, its top initiatives, and a
team of AI agents — then shows you a review screen to approve, edit, or reject. After that you
*steer* it every day with a handful of skills.

**The honest status, up front — read this before you budget your time.** Most of that journey
is **designed but not yet built into this repo.** Right now, exactly one piece runs: the
**genesis engine's reasoning core** (the part that decides which facts are current and drafts
the proposals), and it runs only on **built-in sample data with a fake stand-in for the AI**
("stub" = a canned, scripted reply used in place of a real model so the logic can be tested
without the internet). It does **not** yet connect to your Gmail, it does **not** yet read
your real company, and there is **no review screen, no daily-steer skills, and no installer**
in this repo yet. Those exist as design specs, not as software you can run.

**So what can you actually do today?** Three things, and they're worth doing because they
prove the engine's logic is real and let you read the full plan:

1. **Run the genesis demo** — watch the engine resolve conflicting facts and draft vision +
   agent proposals on sample data (Step 4 below). ~5 minutes. This is the one runnable path.
2. **Read the build docs** — the complete design for the rest of the system (linked in Step 0).
3. **Decide the operator forks** — a few choices only you can make (license, the data-source
   plan) before this becomes a real, cloneable product. Listed at the bottom.

**Why be this blunt?** Because the alternative — a setup guide that reads like everything
works — would waste hours of yours hunting for a "connect Gmail" button that isn't built. The
plan is strong and the hard reasoning core is real and tested (42 automated checks pass). But
"clone it and onboard your company" is **not yet a walkable path.** This guide narrates the
*intended* on-ramp end to end, and at every stage tells you plainly: **BUILT** (runs today),
**PARTIAL** (some real code, gaps remain), or **NOT BUILT** (spec only).

**One thing you must decide that I will not decide for you:** there is no software license on
this repo yet, which legally means "all rights reserved" — nobody but you may use it. You also
haven't chosen how the engine will reach your real data. Both are in **"Decisions only you can
make"** at the end. Until the license question is settled, do not make this repo public.

### The on-ramp at a glance

| Step | Stage | Status today | Time |
|---|---|---|---|
| 0 | Prerequisites (accounts, tools) | partial — you install these | 10–20 min |
| 1 | Get the repo + make it versioned | NOT BUILT (needs `git init`) | 5 min |
| 2 | Connect your data sources | **NOT BUILT** — spec only | — |
| 3 | Run the genesis pass on real data | **NOT BUILT** — spec only | — |
| 3′ | **Run the genesis DEMO (sample data)** | **BUILT — runs today** | 5 min |
| 4 | Review & ratify | PARTIAL — packet built, no approve loop | — |
| 5 | The daily steer loop | **NOT BUILT** — specs only | — |

The single green row — **Step 3′** — is the whole of what runs today. Everything else is the
build ahead, sequenced for whoever builds it (you or an agent) in
[GENERALIZED-REPO-MANIFEST.md](GENERALIZED-REPO-MANIFEST.md).

---

## Step 0 — Prerequisites

### 0a. Tools on your machine

| Tool | Why you need it | Check it's there |
|---|---|---|
| **Python 3.9 or newer** | The genesis engine is Python. | `python3 --version` |
| **Git** | To version the repo and push it to GitHub later. | `git --version` |
| **Claude Code** (or another agent runner) | The *skills* (ramble/vision/etc.) are run through an AI coding agent. Not needed for the genesis demo. | per its own install |
| **GitHub CLI `gh`** (optional) | Convenient if you later publish the repo. | `gh --version` |

**A real Python gotcha, verified on this machine — don't skip.** The genesis code declares
`from __future__ import annotations` at the top of every file, which is a one-line switch that
lets modern type-hint syntax run on **older** Pythons. That means it runs fine on **Python
3.9+**. But on *this* development machine, the newest installed Python (3.14) has a **broken
`pip`/`pytest`** (a damaged `pyexpat` component — `pyexpat` is the built-in XML reader that
the test tool needs to start). The **system Python at `/usr/bin/python3` (3.9.6) is healthy**
and is what actually runs the tests and demos here. The practical rule: **if `pytest` or `pip`
fails with a `pyexpat` error, run the command with `/usr/bin/python3` instead of plain
`python3`.** On your own machine, any healthy Python 3.9+ works — this is a quirk of the dev
box, not a requirement of the code.

> Note: the gating review (B12) flags that an earlier build artifact was pinned to **Python
> 3.14**, which is bleeding-edge and would exclude most users. The current source does **not**
> require 3.14 — it targets 3.9+. When an installer is eventually written, it should target a
> widely-available Python (3.9–3.12), not 3.14.

### 0b. Accounts you'll *eventually* need (NOT yet wired)

When the connectors are built (Step 2, not-built), you'll grant read access to the sources you
want the brain to learn from. None of this is needed for the demo. Plan for:

- **Email** (e.g. Gmail) — the richest source of "who the people are / what's happening."
- **Chat** (e.g. Slack) — *note: the Slack connector was retired and needs a full rebuild; see
  gating B4 and the build-state map. Don't assume Slack works.*
- **Files** (e.g. Google Drive) — working documents.
- **Calendar** — meetings and the people in them.
- **Code host** (e.g. GitHub) — feeds the R&D/engineering pillar.
- **Phone messages** (iMessage / WhatsApp) — macOS-only, partial, hardcoded to one machine
  today.

**Do not create API keys or place any tokens yet.** There is no code in this repo that reads
them, and the **hard limit on secrets** (see
[CLAUDE.md](CLAUDE.md) §4) means tokens must never
land in a committed file. When connectors exist, they'll be placed in a git-ignored location —
and the `.gitignore` that protects them **does not exist yet** (gating B6). Wiring real
accounts now would risk leaking a credential into history with nothing to catch it.

### 0c. Read the plan (5 minutes, high payoff)

Before building anything past the demo, skim, in this order:

1. [PRODUCT-ARCHITECTURE-AND-BUILD-STATE.md](PRODUCT-ARCHITECTURE-AND-BUILD-STATE.md)
   — the whole product mapped, with a per-component "% built" table.
2. [GENERALIZED-REPO-MANIFEST.md](GENERALIZED-REPO-MANIFEST.md)
   — the canonical component + doctrine list and the v1 build order.
3. [SHIP-GATING-REVIEW.md](SHIP-GATING-REVIEW.md)
   — the file-verified list of what blocks making this public (the honest blocker list this
   guide cross-checks against).
4. [SOTA-GENESIS-ENGINE-SPEC.md](SOTA-GENESIS-ENGINE-SPEC.md)
   — how the genesis engine is meant to work end to end.

---

## Step 1 — Get the repo and make it versioned · NOT BUILT (needs one command)

**Status:** This folder is **not a git repository yet** (gating **B1** — verified: `git
rev-parse` reports "not a git repository"). You cannot branch it, version it, or push it to
GitHub until that's fixed.

**The ordering that matters (do not reorder):** create the ignore file **before** the first
commit, so nothing sensitive can ever enter history.

1. **Add a `.gitignore` first** (gating **B6**). At minimum it must ignore: `__pycache__/`,
   `*.pyc`, `.env*`, `*token*.json`, `credentials*.json`, `*.sqlite`, `chat.db`, and
   `genesis/out/` (genesis scratch output). This protects future tokens and the compiled-Python
   clutter from being committed.
2. **Then** `git init` and make the first commit.
3. **Add a `LICENSE`** before going public (gating **B2** — see "Decisions only you can make").

> **This guide does not run `git init` for you.** Per the repo's hard limits, initializing the
> repo and making commits are state changes the operator owns — and the `.gitignore` must be
> authored deliberately first. This is a 5-minute step, but it's yours to take.

---

## Step 2 — Connect your data sources · NOT BUILT (spec only)

**Status: there is no connect step in this repo.** No guided "grant access" flow, no
OAuth/onboarding code, no installer — `find` for connectors/installers returns nothing
(gating **B11**, **B12**; build-state Component 0 ≈ 5%). The connectors that *did* exist live
inside the private source project, are bound to one person's accounts and one laptop's file
paths, and the Slack one was retired entirely.

**What this stage is *meant* to be** (from the architecture doc): a guided step where you grant
read access to email / chat / files / calendar / code / phone, the system scrapes them, and a
generic **sanitize → normalize → dedup** spine ("sanitize" = strip secrets and private bodies;
"normalize" = put every source into one common shape; "dedup" = drop duplicate events) cleans
everything on the way in. That spine is the most reusable existing asset (~75% built) — but it
**is not in this repo yet** and needs lifting + de-coupling from the original company's
defaults.

**What you can do now:** nothing to run. If you're building, this is manifest build-order
items 1 and 6 (lift the ingest spine; rebuild Slack + generic GitHub connectors). Until it
exists, the genesis engine can only run against the **sample corpus** (Step 3′), not your real
company.

---

## Step 3 — Run the genesis pass on your real company · NOT BUILT (spec only)

**Status:** the genesis *reasoning core* is built and runs (that's Step 3′), but running it on
**your real, ingested data** is not possible yet, because:

- there's no connector to produce a real corpus (Step 2), and
- the engine currently uses a **stub model** (a scripted fake) in place of a real LLM, and
- there's no scaffolder to create your empty pillar/agent tree first (gating/build-state
  Component 2; the cold-start scaffold ≈ 2%).

**What the full genesis pass is designed to do** (see
[SOTA-GENESIS-ENGINE-SPEC.md](SOTA-GENESIS-ENGINE-SPEC.md)):
read your entire history in one pass → turn each item into a sourced **claim** → **resolve**
which claims are current (your own word beats an inferred third-party claim; newer beats older;
genuine ties are flagged, not silently merged) → write tidy pillar drafts → **derive your
meta-initiatives** (24-month thrusts) → **propose an agent roster** → build cited per-agent
wikis → assemble a review packet. Every proposal must cite at least one piece of real evidence
or it's dropped (the anti-hallucination rule, enforced in code).

**The good news for whoever builds the rest:** the two hardest parts of that list — the
conflict **resolver** and the **propose-vision/roster** intelligence — are **already built and
tested in this repo.** What's missing to make it run for real is the *front* (connectors,
scaffolder) and swapping the stub for a real model behind the egress gate (below). That's
"wire the existing core to real inputs," not "invent the core."

---

## Step 3′ — Run the genesis DEMO on sample data · **BUILT — this runs today**

**This is the one part you can actually run right now.** It proves the engine's reasoning core
is real: it resolves conflicting facts and drafts vision + agent proposals — on built-in sample
data, using a scripted stand-in for the AI (no internet, no accounts, nothing private).

From the repo, run these (using the healthy system Python per Step 0a):

```sh
# 1) Run the automated checks — proves the logic works (verified: 42 passed)
/usr/bin/python3 -m pytest -q genesis

# 2) The conflict-resolver demo — watch it pick the current fact and archive the stale one
/usr/bin/python3 genesis/genesis_resolver.py

# 3) The full-pipeline demo — drafts pillars + proposes agents + meta-initiatives,
#    and prints the plain-English review packet you'd eventually ratify
/usr/bin/python3 genesis/genesis_pipeline.py
```

**What you'll see, and why it matters:**

- **The tests** end with `42 passed`. They check the *rules*, not the AI's taste: a proposal
  with no evidence is dropped; nothing is ever auto-applied (everything is marked "proposed");
  an agent already on the base team is never re-proposed; and — importantly — feeding the
  **egress gate** a string containing a secret or personal info raises an error
  (`PrivateDataEgressError`), proving private content is blocked from leaving to an outside
  model.
- **The resolver demo** takes 7 sample claims, keeps 4, and archives 2 — e.g. it picks the
  operator-stated launch date over an older one and **archives** (never deletes) the loser. A
  real same-tier clash is marked `disputed` rather than guessed.
- **The pipeline demo** prints a packet that opens **"In plain terms,"** lists what it
  understood per pillar, the agents it proposes (each with a one-line why and an evidence
  count), the meta-initiatives it derived, and a separate evidence section. Draft pillar files
  are written under `genesis/out/` (which your `.gitignore` should exclude).

**Read the genesis code's own quickstart:**
[genesis/README.md](genesis/README.md).

> **Honesty note vs. the gating review.** The gating review (B4/B5, written 2026-06-22 18:51)
> states there is "no product code" and only a stray compiled file. That snapshot is now
> **stale**: the full genesis source landed minutes later (files dated 18:49–18:55) and is
> verified runnable here (42 tests green, both demos produce sensible output). So the gating
> review's "ship-readiness ~3%" headline still holds for *the product as a whole* (no
> connectors, no skills, no installer, no license), but its specific "zero source code" /
> "missing resolver source" claims are **superseded** — the genesis engine core is present and
> works. The related GATED-FOR-OPERATOR fork (rebuild vs. delete the resolver source) appears
> **already resolved** on disk in favor of "the source exists." See the reconciliation note in
> "Decisions only you can make."

---

## Step 4 — Review & ratify · PARTIAL (packet built; approve/edit/reject loop not built)

**Status:** the engine **produces** the review packet (Step 3′ prints it; `review_surface.py`
assembles it as Type-2 plain-English). What does **not** exist is an interactive surface where
you click **ratify / edit / reject** and have those choices written back (gating **B11**;
build-state Component 5 = 0%). Today, everything the engine emits is marked `status="proposed"`
and **applies nothing** — by design, nothing the genesis pass produces is ever treated as fact
until you approve it.

**What ratify is *meant* to be:** you read the packet, approve the agents/initiatives/vision
you want, edit the ones that are close, reject the rest. Approved items then become the
seed of your live brain, and the approved roster turns into real agent identity files +
empty wikis (the persona generator — also not built yet; build-state Component 4 ≈ 7%).

**What you can do now:** read the printed packet from Step 3′ and judge the *quality* of the
proposals — that's the honest preview of what you'd be ratifying. There's no button to press
yet.

---

## Step 5 — The daily steer loop · NOT BUILT (specs only)

Once a brain exists and is ratified, you'd steer it every day with a small set of skills. **None
of these skills are in this repo** — they live in the private source project and are welded to
that company; build-state Component 6 rates them ~40% built *there*, but **0% ported here**
(gating B4). What follows is what each is *for*, so you know the intended rhythm — not commands
you can run today.

| Skill | What it's for (plain English) | Status here |
|---|---|---|
| **`/ramble`** | You talk freely about where things are and where you're going; it breaks that into atomic tasks/decisions, asks clarifying questions until they stop being worth asking, folds it into the plan. The front door for "pour in what's in my head." | NOT BUILT (spec) |
| **`/vision`** | **Top-down.** You sharpen the big initiatives — make them concrete — and that clarity cascades down into projects and tasks. | NOT BUILT (spec) |
| **`/manifest`** | **Bottom-up.** Start from the most fundamental customer problems, do the research, refine, and turn it into a concrete plan/prototype. The opposite direction from `/vision`. | NOT BUILT (spec) |
| **`/morning`** | **The gate.** First thing each day: review the proposals the system generated overnight and **ratify / edit / reject** each one. Nothing acts without passing this gate. | NOT BUILT (spec) |
| **`/pulse`** | **The close-out.** End of a work session: log what happened, update the relevant agent's notes, capture lessons learned, propose improvements to the system itself. | NOT BUILT (spec) |
| **`/close`** | **End of day.** Runs the full pulse, then safely consolidates the day's work branches into one clean line so an overnight run starts from one truth. Born from a real incident where ~95 branches stranded and a routine sent an unasked message — it adds a "did I just send something / strand work?" safety check. | NOT BUILT (spec) |

**The intended daily shape, in one breath:** `/morning` to ratify overnight proposals →
`/ramble`, `/vision`, or `/manifest` to steer during the day → `/pulse` per work session →
`/close` at night. Underneath, a universal task-miner (`atomic-decompose`) quietly reads your
sources and **proposes** tasks (never acts on them) for the morning gate, and a
self-improvement loop folds lessons back in. All of that is **designed, none of it is wired
here yet.**

---

## Where this leaves you (the honest bottom line)

- **Runs today:** the genesis engine's reasoning core, on sample data, with a stubbed AI —
  Step 3′. It proves the hard parts (conflict resolution, evidence-gated proposals, the
  private-data egress block) are real and tested. That is genuinely valuable, and it's the
  thing to demo.
- **Designed, not built:** connecting real sources, running genesis on your real company, the
  click-to-ratify review surface, the persona generator, and every daily-steer skill —
  plus the packaging (license, `.gitignore`, `git init`, installer, skill namespace) that
  makes it a thing a stranger can clone and run.
- **The build path** for closing that gap is sequenced in
  [GENERALIZED-REPO-MANIFEST.md](GENERALIZED-REPO-MANIFEST.md)
  (the v1 build order) and gated by
  [SHIP-GATING-REVIEW.md](SHIP-GATING-REVIEW.md)
  (the blocker list). The cheapest high-signal next moves are the minutes-long packaging steps
  (`.gitignore` → `git init` → license → README → root `CLAUDE.md`, already present) and then
  the real build (lift the ingest spine → wire genesis to real data behind the egress gate).

---

## Decisions only you can make (GATED-FOR-OPERATOR)

These are forks this guide will **not** decide for you. They block making the repo public.

1. **Choose a software license** (gating **B2**). With no `LICENSE` file, the legal default is
   "all rights reserved" — nobody but you may use, copy, or modify this. For a tool like this,
   the conventional permissive choices are **MIT** (simplest) or **Apache-2.0** (also grants
   patent rights, worth weighing given the genesis-engine IP). **Pick one before going public.**
   *(Until then, keep the repo private.)*

2. **Decide how the engine reaches your real data — and which AI it uses.** The engine runs
   today against sample data with a fake model. Going live means (a) building/lifting the
   connectors (Step 2) and (b) choosing the real model the genesis pass calls. Whatever you
   choose, the **data-boundary rule** stands: every call to an outside model must pass through
   the egress gate, and raw private company content must never leave (this is already enforced
   in code and tested). This is a plan-and-build decision, not a one-liner.

3. **Reconciliation to confirm (informational — already resolved on disk, flagged for your
   awareness).** The gating review's open **GATED-FOR-OPERATOR** item asked whether the missing
   `genesis_resolver.py` source had been *lost* or *intentionally cleaned*. As of now the
   **full source is present and runnable** (`genesis/genesis_resolver.py` +
   `genesis_pipeline.py` + siblings + tests, all dated 2026-06-22 18:49–18:55, 42 tests green).
   So that fork reads as **resolved in favor of "rebuilt/kept."** This guide changed nothing in
   `genesis/`; if that resolution was *not* intentional, that's yours to confirm. Either way,
   the one durable to-do from it: the old compiled `.pyc` must never be committed — your
   `.gitignore` (Step 1) handles that.

---

*Rails honored: wrote only this file under
`docs/`. No `git init`/commit/push/merge, no
deletes, no edits to existing files, no external sends, no money/secrets, no access to the
private source brain. Built-vs-not status for every stage was verified against the live
`genesis/` files (42 tests pass; resolver + pipeline demos run) and cross-checked against
`SHIP-GATING-REVIEW.md`. The one ambiguity encountered — the gating review's resolver-source
fork — was found already resolved on disk and is recorded above as GATED-FOR-OPERATOR rather
than acted on.*
