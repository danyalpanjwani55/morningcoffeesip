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
> in-progress build. **The genesis engine's brain — the resolver + the
> proposal/intelligence layer — is real, runnable, and tested today** (74
> passing tests; see [Quickstart](#quickstart)). The rest of the system (the
> data connectors, the five steering commands, the installer, the guided
> setup) is **specified in detail but not yet ported into this repo.** What you
> can run right now is the heart, not the whole body. See
> [What works today vs. what's specified](#what-works-today-vs-what-is-specified).

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
| **Genesis claim resolver** (decide which conflicting fact is current; archive the loser) | **Runs, tested** | [`genesis/genesis_resolver.py`](genesis/genesis_resolver.py) |
| **Genesis intelligence layer** (derive big bets, propose the agent roster, build the review packet, end-to-end pipeline) | **Runs, tested** | [`genesis/genesis_pipeline.py`](genesis/genesis_pipeline.py) + siblings |
| **The data-boundary gate** (refuse to send private data out) | **Runs, tested** | [`genesis/genesis_contracts.py`](genesis/genesis_contracts.py) (`EgressGate`) |
| The five steering commands (ramble/vision/manifest/morning/pulse) | **Specified, not yet here** | — |
| The data connectors (email, chat, files, calendar, code) | **Specified, not yet here** | — |
| The guided setup + installer | **Specified, not yet here** | — |
| The self-improvement loop | **Specified, not yet here** | — |

The full plan, component by component, lives in the public docs:

- **[`docs/SYSTEM.md`](docs/SYSTEM.md)** — the component map: every feature, agent,
  skill, and tool, and the percent-built state of each piece.
- **[`docs/SETUP.md`](docs/SETUP.md)** — how to stand the system up, the genesis
  engine flow, and how to run it today.
- **[`CLAUDE.md`](CLAUDE.md)** — how work happens in this repo: the doctrine, the
  coding rules, and the hard limits. Read it before contributing.

---

## Quickstart

Two minutes, no setup, no accounts, no data of your own required. This runs the
genesis engine's brain against a tiny built-in example so you can watch it work.

**You need:** Python 3.9 or newer. That's it — the code is pure standard library
(no `pip install`, no dependencies).

```sh
# 1. Get the code
git clone <this-repo-url>
cd morningcoffeesip/genesis

# 2. See the genesis engine run end-to-end on a built-in sample.
#    It reads a handful of made-up "company events," resolves two
#    conflicting facts, proposes a big bet and an agent, and prints
#    the plain-English review packet you'd approve.
python3 genesis_pipeline.py

# 3. (Optional) Watch just the conflict-resolver decide which of two
#    disagreeing facts is the current one.
python3 genesis_resolver.py

# 4. (Optional) Run the test suite — proves every rule above holds.
python3 -m pytest -q
```

What you'll see from step 2: a section titled **"In plain terms,"** then *here's
what I understood* (a couple of knowledge pillars), *agents I propose* (with the
one-line reason), *big bets I derived*, and an *evidence* block tying every
suggestion back to its source. Every line ends in `status: proposed` — because
nothing is ever applied without your say-so.

> If `python3 -m pytest` reports it can't find `pytest`, install it with
> `python3 -m pip install pytest`, or run the tests with your project's own
> Python environment. The product code itself needs nothing beyond the standard
> library.

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

The build order (full detail in [`docs/SYSTEM.md`](docs/SYSTEM.md)): finish the
genesis engine's front half (the data connectors and the clean-up spine), port
the five steering commands and the self-improvement loop, wire the guided
setup and installer, and ship the data-boundary gate ahead of any real data.
The goal is the headline journey, walkable by a stranger end to end:
**connect your tools → it births your brain → you review → you steer.**

---

## License

Not yet chosen — **treat this as "all rights reserved" for now** (no license
means no one may legally copy, use, or modify it). A permissive license will be
added before any public release.
