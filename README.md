# MorningCoffeeSip

**A company brain you clone, point at your own data, and run — and a swarm of
agents that keeps making it (and itself) better while you sleep.**

You connect your sources (email, chat, files, calendar, code). A **genesis
engine reads your company into existence** — who the people are, what it's
trying to do, since day one — drafts your knowledge base, proposes your vision,
your big bets, and a roster of specialist agents, and hands you a plain-English
review screen to approve. From there you steer with a handful of simple
commands, and a self-improvement loop keeps the whole thing sharpening over
time.

> **Status (be honest with yourself before you clone):** this is an early,
> in-progress build, but **the headline journey now runs end to end on one
> command.** `python3 run.py` walks a pile of raw sources all the way through —
> ingest (clean + de-secret) → genesis (resolve facts, draft pillars, propose
> your vision and agent roster) → a plain-English review packet → your ratify
> gate → a cited wiki for each agent you approve. It runs on a bundled sample
> with **no setup, no accounts, and no data of your own** (337 passing tests;
> see [Quickstart](#quickstart)), or on **your own folder of notes/mail** via
> `--sources` (see [Connecting your own data](#connecting-your-own-data)).
>
> What's **still missing for a stranger to run this on their own company at
> full fidelity:** the source *connectors* that auto-pull from Gmail / Slack /
> Drive (today you point it at a folder of exported `.md`/`.txt`/`.eml`), a
> real model wired behind the privacy gate (today a built-in offline model does
> the reasoning), and a click-to-ratify UI (today ratify is a terminal
> prompt). See
> [What works today vs. what's specified](#what-works-today-vs-what-is-specified).
>
> Two ingest lanes already go further than the export-a-folder path: your
> **iMessage + WhatsApp** are read on your Mac by a runnable local agent
> (allowlisted, sanitized, proposals-only —
> [`docs/INGEST-ARCHITECTURE.md`](docs/INGEST-ARCHITECTURE.md)), and the
> **email / Drive / calendar** half is split in two: the **processing** code
> (sent-correspondent filter → shared spine → genesis → proposals) ships and is
> tested here ([`ingest/cloud/`](ingest/cloud/), runnable as
> `python -m ingest.cloud.refresh`); only the **auth + pull** stays a scheduled
> cloud routine you stand up from a recipe
> ([`docs/CLOUD-ROUTINE.md`](docs/CLOUD-ROUTINE.md)), because that part is welded
> to whichever assistant platform you use.

---

## In plain terms

Imagine hiring a chief of staff on your first day who has somehow already read
every email, every message thread, every shared doc, and every meeting note
your company has ever produced — and who, by tomorrow morning, hands you a tidy
binder: *here's who everyone is, here's what we're actually working on, here are
the three big things I think matter, and here are the five specialists I'd hire
to own them.* You read it, cross out what's wrong, and say go.

That's what this is, except the chief of staff is software, the binder writes
itself from your real data, and a small team of those specialists then keeps the
binder current — and keeps getting better at the job — without you babysitting
them.

**Why it's built this way (the one idea that explains everything):** the part
worth reusing is **not** a pre-filled brain. Every company's brain is different,
so a generic one would be useless. What's reusable is **the machine that
manufactures a brain from a brand-new company's raw data.** So the first thing
that happens when you run it isn't "fill in a template" — it's "read your actual
company and build the thing from scratch." **Ingest (pulling in your data) isn't
a feature here. It's the ignition.**

**What you have to decide, always.** The system proposes; you approve. It never
files, sends, buys, or publishes anything on its own, and it never lets your
private data leave to an outside AI model. Those are hard rules baked into the
code, not promises — see [The hard rules](#the-hard-rules-built-in-not-promised).

---

## The value, in one breath

- **Day-one memory instead of month-three.** A solo founder's biggest tax is
  that everything they know lives in their head and their inbox. This turns that
  scattered pile into a single, searchable, *cited* brain — every fact traceable
  back to the email or message it came from.
- **A team of one becomes a team of many.** The agent roster gives you
  specialists (a go-to-market lead, a product lead, whatever your data says you
  need) that each own a domain and keep its corner of the brain current.
- **It compounds.** A self-improvement loop notices what went wrong, proposes a
  fix to its own rules, and — once you approve it — the whole system is a little
  sharper the next day. You're not maintaining a tool; you're growing one.
- **It's private by construction.** Your raw company data never gets shipped off
  to a third-party model. The boundary is enforced in code and fails *closed*
  (when in doubt, it treats data as private and refuses to send it).
- **You own all of it.** It's your repo, your data, on your machine. Nothing is
  rented, nothing phones home.

---

## How it's built (the architecture, in words)

Picture an assembly line that runs left to right. Raw company data goes in one
end; an approved, living company brain comes out the other; and a loop underneath
feeds lessons back to the start.

```
   YOUR SOURCES                 THE GENESIS ENGINE                    YOU
   (email, chat,      ┌──────────────────────────────────┐
    files, calendar,  │  1. INGEST    pull it all in,     │
    code)  ───────────┤               then clean it:      │
                      │               strip secrets,      │
                      │               de-duplicate,       │
                      │               normalize           │
                      │                      │            │
                      │  2. CLAIMS    turn each source    │
                      │               into dated, sourced │
                      │               facts               │
                      │                      │            │
                      │  3. RESOLVE   when two facts       │   ← the part that
                      │               disagree, keep the  │     runs & is tested
                      │               current one (your   │     today
                      │               word > a primary    │
                      │               record > hearsay;   │
                      │               newer wins in a tie),│
                      │               archive the loser —  │
                      │               never delete it      │
                      │                      │            │
                      │  4. WRITE     fill the knowledge   │
                      │               pillars; build each  │
                      │               agent's *cited* wiki │
                      │                      │            │
                      │  5. PROPOSE   derive the big bets; │   ← also runs &
                      │               propose the agent    │     tested today
                      │               roster — each one    │
                      │               must cite real       │
                      │               evidence or it's     │
                      │               dropped              │
                      │                      │            │
                      │  6. REVIEW    a plain-English      │──────►  approve /
                      │               "here's what I       │         edit /
                      │               understood, here's   │         reject
                      │               what I propose"      │
                      └──────────────────────────────────┘            │
                                                                       ▼
                      ┌──────────────────────────────────┐   7. STEER it daily
                      │  THE STEERING COMMANDS            │      with 5 commands:
                      │  ramble · vision · manifest ·     │◄─────  (specified;
                      │  morning · pulse                  │         not yet ported)
                      └──────────────────────────────────┘
                                      │
                      ┌───────────────▼──────────────────┐
                      │  THE SELF-IMPROVEMENT LOOP        │   ── feeds lessons
                      │  notice → propose a rule change → │      back to the top,
                      │  you approve → the system is      │      so it compounds
                      │  smarter tomorrow                 │
                      └──────────────────────────────────┘
```

**Three guardrails sit across the whole line:**

1. **The data-boundary gate.** Before anything is sent to an outside AI model, a
   classifier checks it. Secrets, passwords, personal info, contract text, or
   anything it can't confidently call safe → it refuses to send. Fail-closed by
   design.
2. **Verify-before-relay.** Every proposal (a big bet, a new agent) must cite at
   least one real piece of evidence from your data, or it's silently dropped
   before it ever reaches you. The AI can't invent a recommendation out of thin
   air.
3. **Propose, don't apply.** The genesis pass *writes nothing into your brain.*
   It produces a review packet and waits. Approving is your move, every time.

**The five steering commands** (your daily controls, once they're ported):

| Command | What it's for |
|---|---|
| **ramble** | Talk out loud about where you are and where you're headed; it captures it and folds it into the plan. |
| **vision** | Sharpen your big bets from the top down — make the strategy concrete. |
| **manifest** | Work bottom-up from a real problem to a concrete plan and prototype. |
| **morning** | The daily gate — review what the system proposed overnight; approve, edit, or reject. |
| **pulse** | The end-of-session close-out — record what happened, update the brain, capture lessons. |

---

## What works today vs. what is specified

Be precise about this so you're not surprised after you clone.

| Part | State | Where |
|---|---|---|
| **The end-to-end on-ramp** (one command: ingest → genesis → review → ratify → cited wikis) | **Runs, tested** | [`run.py`](run.py) |
| **Genesis claim resolver** (decide which conflicting fact is current; archive the loser) | **Runs, tested** | [`genesis/genesis_resolver.py`](genesis/genesis_resolver.py) |
| **Genesis intelligence layer** (derive big bets, propose the agent roster, build the review packet, end-to-end pipeline) | **Runs, tested** | [`genesis/genesis_pipeline.py`](genesis/genesis_pipeline.py) + siblings |
| **The data-boundary gate** (refuse to send private data out) | **Runs, tested** | [`genesis/genesis_contracts.py`](genesis/genesis_contracts.py) (`EgressGate`) |
| **The ingest spine** (sanitize → normalize → dedup) + file/email adapters | **Runs, tested** | [`ingest/`](ingest/) |
| **The installer / scaffolder** (fresh clone → empty brain skeleton; namespaces skills) | **Runs, tested** | [`install.py`](install.py) |
| **The five steering commands** (ramble/vision/manifest/morning/pulse) + `close` + `atomic-decompose` | **Ported (skill specs), de-welded** | [`skills/`](skills/) |
| **The self-improvement loop** (fold · ratchet · skill-deltas) | **Runs, tested** | [`loop/`](loop/) |
| **The local message lanes** (iMessage + WhatsApp → allowlisted, sanitized, proposals-only — the local Mac sync agent) | **Runs, tested** (needs a one-time macOS Full Disk Access grant) | [`ingest/local/`](ingest/local/) · [`docs/INGEST-ARCHITECTURE.md`](docs/INGEST-ARCHITECTURE.md) |
| The **cloud routine** for email / Drive / calendar — **processing** half (Gmail sent-filter → shared spine → genesis → proposals) | **Runs, tested** (`python -m ingest.cloud.refresh` on a connector dump) | [`ingest/cloud/`](ingest/cloud/) |
| The **cloud routine** — **auth + pull** half (the scheduled read-only connector access) | **Specified, not shipped code** — stand it up from the recipe (welded to your assistant platform) | [`docs/CLOUD-ROUTINE.md`](docs/CLOUD-ROUTINE.md) |
| The source **auto-pull connectors** (one-click Gmail / Slack / Drive / code host) | **Not yet — point `--sources` at an exported folder instead** | — |
| A real model wired behind the privacy gate (today a built-in offline model reasons) | **Not yet — inject your own via the `LLM` protocol** | — |
| A click-to-ratify review **UI** | **Not yet — ratify is a terminal prompt today** | — |

The full plan, component by component, lives in the public docs:

- **[`docs/SYSTEM.md`](docs/SYSTEM.md)** — the component map: every feature, agent,
  skill, and tool, and the percent-built state of each piece.
- **[`docs/SETUP.md`](docs/SETUP.md)** — how to stand the system up, the genesis
  engine flow, and how to run it today.
- **[`docs/CONNECT.md`](docs/CONNECT.md)** — point the on-ramp at your own
  company's data (the export-a-folder path you can run now).
- **[`docs/INGEST-ARCHITECTURE.md`](docs/INGEST-ARCHITECTURE.md)** — the local↔cloud
  ingest split: the local Mac agent for iMessage/WhatsApp, the allowlist, the
  refresh cadence, and the non-Mac fallback.
- **[`docs/CLOUD-ROUTINE.md`](docs/CLOUD-ROUTINE.md)** — stand up the cloud half
  (email/Drive/calendar) from a recipe + paste-in prompt.
- **[`CLAUDE.md`](CLAUDE.md)** — how work happens in this repo: the doctrine, the
  coding rules, and the hard limits. Read it before contributing.

---

## Quickstart

Two minutes, no setup, no accounts, no data of your own required. **One command**
walks the whole on-ramp on a tiny built-in sample company so you can watch a
brain get born end to end.

**You need:** Python 3.9 or newer. That's it — the code is pure standard library
(no `pip install`, no dependencies).

```sh
# 1. Get the code
git clone <this-repo-url>
cd morningcoffeesip

# 2. Walk the WHOLE on-ramp on the bundled sample, end to end:
#    ingest (clean + strip secrets) → genesis (resolve facts, draft pillars,
#    propose your vision + agent roster) → the plain-English review packet →
#    your ratify gate → a cited wiki for each agent you approve.
python3 run.py --auto-ratify        # non-interactive: approves every proposal
#  python3 run.py                    # interactive: it asks you per proposal

# 3. (Optional) Run the test suite — proves every rule holds (337 passing).
python3 -m pytest -q
```

What you'll see from step 2: the seven stages print in order, then a packet
titled **"In plain terms"** — *here's what I understood* (your knowledge
pillars), *agents I propose* (each with a one-line reason and an evidence
count), *meta-initiatives I derived*, and an *evidence* block tying every
suggestion back to its source. Every line ends in `status: proposed`, and a
cited DRAFT wiki is built only for the agents you ratify — because **nothing is
ever applied without your say-so.**

Want to look at just one organ?

```sh
python3 genesis/genesis_pipeline.py   # the genesis reasoning core, on a fixture
python3 genesis/genesis_resolver.py   # just the conflict-resolver, step by step
```

> If `python3 -m pytest` reports it can't find `pytest`, install it with
> `python3 -m pip install pytest`, or run the tests with your project's own
> Python environment. The product code itself needs nothing beyond the standard
> library. (On some newer Homebrew Pythons `pytest` won't start due to a broken
> `pyexpat`; `/usr/bin/python3 -m pytest -q` is the reliable fallback.)

---

## Connecting your own data

The Quickstart runs on a bundled fictional company. To point the same on-ramp at
**your** company, hand `run.py` a folder of your own exported notes and mail —
no accounts or API keys needed:

```sh
python3 run.py --sources /path/to/your/exported_notes_and_mail
```

`--sources` accepts either a folder with `notes/` (`.md`/`.txt`) and `mail/`
(`.eml`) subfolders, or a single flat folder of those files. The ingest spine
**strips secrets and personal info before anything is read** (you'll see them
counted as `dropped private` in the run summary), so a stray API key or SSN in a
note never reaches the model or a wiki.

What's *not* automated yet (and what to expect): there are **no live connectors**
that auto-pull from Gmail / Slack / Drive — you export to a folder for now. The
reasoning runs on a small **built-in offline model** by default; to use a real
model, inject one implementing the `complete(system, user, *, max_tokens)`
protocol via `run_journey(..., llm=your_model)` (see the `LLM` protocol in
[`genesis/genesis_contracts.py`](genesis/genesis_contracts.py) and the
`OfflineGenesisLLM` example at the top of [`run.py`](run.py)). Every model call
is forced through the privacy gate regardless of which model you use. The full
on-ramp, source-by-source and decision-by-decision, is documented in
[`docs/CONNECT.md`](docs/CONNECT.md).

---

## The hard rules (built in, not promised)

These are enforced in code (and stated in [`CLAUDE.md`](CLAUDE.md) for every
agent and contributor). The system will stop and ask rather than cross any of
them:

1. **No external sends.** No emails, posts, filings, or anything the outside
   world can see, without your explicit go.
2. **No moving money** and no touching credentials or secrets.
3. **No private data leaving to an outside model.** Sanitized on the way in,
   classified on the way out, fail-closed when unsure.
4. **No destructive operations** — no force-deletes, no rewriting history, no
   mass deletion.

If it hits a fork that's irreversible or ambiguous, it stops and surfaces the
decision to you. A truthful "I stopped here, and here's why" beats a confident
wrong move.

---

## Where this is headed

The on-ramp spine is in: the ingest clean-up pipeline, the genesis engine, the
five steering commands, the self-improvement loop, the installer, and the
data-boundary gate all live in this repo and pass their tests. What remains to
make the headline journey **walkable by a stranger on their own live company**
(full detail in [`docs/SYSTEM.md`](docs/SYSTEM.md)): the source **connectors**
that auto-pull from Gmail / Slack / Drive (today you export to a folder and pass
`--sources`), wiring a **real model** behind the privacy gate (today a built-in
offline model reasons), and a **click-to-ratify UI** (today ratify is a terminal
prompt). The goal stays the same headline:
**connect your tools → it births your brain → you review → you steer.**

---

## License

This repo ships an **[Apache-2.0](LICENSE)** license as a **reversible
placeholder** — a permissive license that also carries a patent grant. The real
open-source-vs-closed decision is **gated to the operator and not yet final**;
the reasoning, the fork, and the one caveat (permissive-now → closed-later is
hard to reverse) are laid out in
[`docs/LICENSE-NOTE.md`](docs/LICENSE-NOTE.md). Until that call is settled, treat
publication as the operator's to make — nothing here is on the internet yet.
