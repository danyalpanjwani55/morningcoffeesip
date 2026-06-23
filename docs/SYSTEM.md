# SYSTEM.md — what MorningCoffeeSip is made of

*The component map for someone who cloned this repo and wants to understand the
machine before running it. Type-2 (FOR-THE-OPERATOR): plain English, opens "in
plain terms." The **what-to-build** authority is
[GENERALIZED-REPO-MANIFEST.md](GENERALIZED-REPO-MANIFEST.md);
the **product flow** is
[PRODUCT-ARCHITECTURE-AND-BUILD-STATE.md](PRODUCT-ARCHITECTURE-AND-BUILD-STATE.md);
the **genesis engine** is
[SOTA-GENESIS-ENGINE-SPEC.md](SOTA-GENESIS-ENGINE-SPEC.md).
This doc explains the pieces; those docs justify and sequence them.*

---

## In plain terms

MorningCoffeeSip is a **company brain you clone and run on your own company.**
You connect your sources (email, chat, files, calendar, code); a **genesis
engine reads your company into existence** from that data — who your people are,
what you're trying to do, since day one — fills a set of knowledge files,
proposes your vision, your 24-month thrusts, and a **team of AI agents**, and
shows you a review screen to approve. From there you steer with a handful of
voice/text skills, and the system keeps getting smarter on its own.

The reusable thing is **not** a filled-in brain — that's custom to each company,
by design. What's reused is **the machine that manufactures a brain from a new
company's raw data.** Ingest isn't a feature; it's the ignition.

This doc breaks the machine into its four kinds of parts:

1. **Features** — the capabilities a user gets (ingest → genesis → review →
   steer → self-improve → overnight-safety).
2. **Agents** — the AI "staff." A small **base roster** ships as a template;
   genesis **proposes** the company-specific additions from your data.
3. **Skills** — the named commands that drive the system: `ramble`, `vision`,
   `manifest`, `morning`, `pulse`, `close`, plus `atomic-decompose` and the
   genesis engine.
4. **Tools** — the plumbing the above run on: the **connectors** (read your
   sources), the **LLM** (the model that does the reasoning), and **git** (the
   versioned store + the safety/consolidation layer).

**One honest caveat up front.** This repo is mid-build. The **genesis engine's
core is real code that exists here and passes its tests today** (the resolver +
the intelligence layer, 42 tests green — see [§ Build state](#build-state)).
Most of the **skills, the agents, and the ingest connectors are specified here
but their code still lives inside the private brain they were extracted from** —
they are described below as designed, with their build-state marked. When this
doc says a piece "exists," it means in *this* repo; when it says "specified," it
means the design is settled but the code hasn't been ported/de-welded yet. The
definitive blocker list is
[SHIP-GATING-REVIEW.md](SHIP-GATING-REVIEW.md).

---

## 1. Features — what the system does

The product is one flow, top to bottom. Each stage is a feature; each is owned
by skills/tools described later. (Full diagram in
[PRODUCT-ARCHITECTURE-AND-BUILD-STATE.md](PRODUCT-ARCHITECTURE-AND-BUILD-STATE.md).)

| # | Feature | What it is | Why it exists | How it's wired |
|---|---|---|---|---|
| 0 | **Connect** | You grant access to your sources: email, chat, files, calendar, code. | A brain needs raw material. This is the ignition — nothing downstream runs without it. | The **connectors** (§4) read each source. v1 places credentials by hand (gitignored); a guided "grant access" onboarding is future work. |
| 1 | **Ingest** | Each source is scraped, then run through a generic **sanitize → normalize → dedup** spine, then routed to where its evidence belongs. | Raw data is messy, duplicated, and full of secrets/2FA codes. The spine makes it uniform and safe *before* anything reads it. | The privacy gate strips secrets on the way in; normalize/dedup make sources look the same; routing sends each item to the right pillar/agent. *(Spine specified; ~75% reusable, still in the private brain.)* |
| 2 | **Genesis (broad first pass)** | Build the brain **from nothing**: scaffold the empty knowledge tree, auto-populate the pillars from your whole history, and auto-derive each pillar's **meta-initiatives** (24-month thrusts). | The earlier insight that reframes the product: the *brains* should be custom per company — what's reusable is the **factory** that stamps one out from data. | The **genesis engine** (§3) does this. Its core (claim-resolver + intelligence layer) is **real code in this repo**. |
| 3 | **Deep ingest (understand + propose)** | Read the recent past to identify the live working documents, the immediate initiatives, and **which extra agents you need** beyond the base roster. | A company isn't just facts — it's the work in flight and the team shape. This stage proposes both. | `atomic-decompose` (§3) mines tasks; the genesis **roster proposer** proposes agents; both are **proposals only**, gated at the morning review. |
| 4 | **Build the custom brain** | Generate each proposed agent's identity file and reorganize the document pile into **cited per-agent wikis** ("a source behind every claim"). | An agent is only trustworthy if every fact it holds points back to where it came from. | The **cited-wiki builder** (the `train-on-docs` craft) does this. *(Specified; the per-doc cited-page craft is strong but the auto-routing orchestration is future work.)* |
| 5 | **Review surface** | A plain-English screen: "here's what I understood · here are the agents I propose · here are the docs I'd reorganize" → you ratify, edit, or reject. | Nothing should land in your brain without your say-so. This is the human gate. | The genesis **review surface** is **real code in this repo** (`review_surface.py`) — it emits a Type-2 "In plain terms" Markdown packet where **every item is `status="proposed"`** and shows its evidence count. |
| 6 | **Steer** | Run the company day-to-day with five skills: **ramble · vision · manifest · morning · pulse**. | Once the brain exists, you drive it: pour in thinking, sharpen the vision, derive actions, gate the day, close it out. | The **five steering skills** (§3). *(Specified; all five exist with strong logic in the private brain, welded to that company.)* |
| 7 | **Self-improvement loop** | The system folds new knowledge into the right agent, runs a **skill-deltas ratchet** (it proposes its own improvements), and queues tomorrow's tasks for the morning gate. | A system that doesn't get smarter rots. This is the flywheel. | `fold` + the skill-delta ledger + `task-proposals → morning`. *(Specified; the detect→draft→gate ratchet is real and rare, but operator-gated and not yet ported.)* |
| 8 | **Overnight safety + consolidation** | When the autonomous loop runs while you sleep, the next morning **safely gathers its branches** and **catches any rogue action** (e.g. a job that emailed someone unasked). | Born from a real incident: a scheduled job sent an unasked email at 4:59am, and ~95 work-branches stranded because the auto-merge pinned one branch name. **Any solo founder running one nightly job hits this on day one.** | `/close` consolidates branches one-at-a-time; `/morning` adds a **routine-send detector** + a branch sweep. The newest of the three NEW components the earlier audit missed. *(Specified.)* |

**Where the value concentrates:** features 2–5 (the genesis half — birth a brain
from data) are *the product*. Features 6–8 (the steering half — run a brain) are
a strong, mostly-built system that currently assumes a brain already exists.

---

## 2. Agents — the AI staff

An **agent** is a specialist persona the system runs *as* — it has an identity
(who it is, its domain, how it talks) and a **wiki** (its cited knowledge in one
lane). You don't talk to one giant assistant; you route work to the right
specialist, and a coordinator composes the company-wide view.

### The base roster (the shipped template)

A **base roster of ~4 illustrative agents** ships as a reusable *template* —
not filled-in people, just the slots a typical company starts from:

| Slot | Lane (illustrative) | Why a dedicated agent |
|---|---|---|
| **Coordinator / chief-of-staff** | Orchestration | Composes the company-wide view from the other agents' wikis, routes work, escalates conflicts. Use it **first** for any multi-agent or autonomous task. It has no domain wiki of its own — by design. |
| **Specialist A** | e.g. legal/business | One deep lane (regulatory, contracts, finance, IP). |
| **Specialist B** | e.g. product | The "why" lane (positioning, thesis, the market). |
| **Specialist C** | e.g. software/build | The build lane (the app, the pipeline, the tooling). |

> The names of the example agents (Ava, Connor, Potter, Sally, etc.) are
> **deliberately not shipped** — a filled roster doesn't travel. The template is
> just "here are the kinds of slots; genesis fills the rest from your data."
> *(See manifest §(iii): "ship a base-roster template + genesis proposes
> additions; filled personas don't travel.")*

### How genesis proposes additions

This is the part that makes the roster *yours*. After ingest, the **roster
proposer** (`roster_proposer.py`, real code in this repo) reads your corpus and:

- **Clusters recurring work** into candidate specialist domains (the LLM does
  the clustering; see §4).
- **Proposes a new agent only when the evidence is real.** A domain must have
  **≥ 3 *distinct* anchored signals** (`MIN_EVIDENCE = 3`) before an agent is
  proposed — counted by distinct source+location, so one chatty thread can't be
  inflated into "recurring." This is the anti-hallucination floor: the system
  **never invents a role from thin air.**
- **Never re-proposes a base-roster agent** (case-insensitive slug match).
- **Carries the evidence.** Every proposed agent ships with the exact sources
  that justify it (`suggested_wiki_sources`) — verify-before-relay as code.

The output is a list of **proposals** (`status="proposed"`), each with its
"why" and its references. Nothing is created until you ratify it on the review
surface. *(This is real, tested code — see the `roster_proposer` cases in
[§ Build state](#build-state).)*

**Why agents at all:** a single model holding everything is shallow and
unciteable. Splitting into specialists with cited wikis means each lane is deep,
each fact is traceable, and a reviewer-twin can check a builder without
green-lighting itself (the **twin-peer** rule, §3 `pulse`/doctrine).

---

## 3. Skills — the named commands that drive everything

A **skill** is a packaged capability you invoke by name (a "slash command" like
`/ramble`). Each bundles its instructions and rails so any agent can run it the
same way. Below are the skills the manifest names for the generalized repo,
grouped by job.

### 3a. The five steering skills (the drive layer)

These are how you *run* the company once a brain exists. They split into
**top-down** (you push clarity down) and **bottom-up** (the system pulls actions
up), with a daily open/close.

| Skill | What it is | Why it exists | How it's wired |
|---|---|---|---|
| **`ramble`** | You just talk — for as long as you like — about where you are and what you're figuring out. It decomposes the ramble into atomic items, asks follow-up questions until they stop being worth it, folds the result into the plan, and hands off to the autonomous run. | The lowest-friction way to get what's in your head into the system. Speech in, structured plan + queued work out. | Front-end of the trio; calls `atomic-decompose` (§3c) on what you said, repopulates the task board, ends with the "go get coffee" handshake that launches the autonomous loop. |
| **`vision`** | **Top-down** clarifier: you sharpen the meta-initiatives (the big 24-month thrusts), make them concrete, and cascade that clarity down into projects → subprojects → tasks. | Sometimes you steer from the top: "here's where we're going," pushed downward into real work. | Edits existing meta-initiatives and cascades; feeds the autonomous loop. The opposite direction from `manifest`. |
| **`manifest`** | **Bottom-up** engine: start from the most fundamental customer problems, do maximum research, refine → spec → design → bring a **prototype** to life. | The complement to `vision`: build *up* from real problems instead of *down* from strategy. | Extends an existing plan with researched, spec'd, prototyped work; produces the units the autonomous loop builds. |
| **`morning`** | The **gate**: first interaction of the day. Review the overnight proposals (ratify / edit / reject); ratified ones become real tasks; rulings are recorded. | A human checkpoint so nothing the system did overnight lands without you. Also where the **overnight-safety** sweep + **routine-send detector** live (feature 8). | Reads the proposal queue + the overnight reconciliations *before* asking you anything ("answer from the record first"), then surfaces only what needs your call. |
| **`pulse`** | The **close-out**: end of a work session, drop a sanitized pulse per active lane, update the owning agent's wiki, run a short "what did we learn" retrospective, and emit **skill-delta** proposals (the system proposing its own improvements). | Capture the day's knowledge + learnings while they're fresh, routed to the right agent, so the brain compounds. | Writes per-lane pulses, folds them to agents, and feeds the self-improvement ratchet (feature 7). |

> **`close` vs `morning` vs `pulse`.** `pulse` is the per-session close-out.
> `close` (§3b) is the *end-of-day* close that runs the full pulse **and then**
> consolidates the day's git branches into one clean line. `morning` is the
> next-day open + gate. The cycle is: work → `pulse` (each session) → `close`
> (end of day) → overnight loop → `morning` (gate) → work.

### 3b. `/close` — end-of-day consolidation (a NEW component)

**What it is:** the end-of-day ritual. It runs the full `pulse` close-out, then
**enumerates every unmerged branch**, classifies each (substantive vs throwaway
vs in-flight), **merges the newest-per-routine one at a time**, pushes, and
**flags-only** for cleanup (it never auto-deletes).

**Why it exists:** the autonomous overnight loop produces many branches. Without
a disciplined gather, they strand — and a naive "merge them all" auto-merge
pinned to one branch-name silently dropped ~95 of them in a real incident. This
is the component that makes "the loop ran while I slept" *safe to wake up to.*

**How it's wired:** de-welded from the source company via a `$BRAIN_ROOT`
variable (no hardcoded paths) and with backend-DB specifics dropped. It pairs
with the `claude/**` **auto-merge template** (match *all* routine branches, not
a pinned prefix — that pin was the bug). *(Specified; one of the three NEW
components the earlier product audit had no row for.)*

### 3c. `atomic-decompose` — the universal task-intake (a NEW component)

**What it is:** the engine that turns *any* source — a meeting transcript, an
email, an iMessage/WhatsApp thread, **even a terminal transcript** — into every
explicit *and implied* atomic task/decision, each stamped with owner · horizon ·
done-bar · evidence-anchor.

**Why it exists:** decisions and to-dos hide inside conversations. This mines
them automatically at **every cadence point** (pulse / fold / close / refresh),
so nothing slips. It's a materially bigger intake engine than the original audit
credited.

**How it's wired:** **proposals-only** — everything it emits is a *proposed*
entry to the task queue, **gated at the `morning` review** (it never
auto-commits a task). It reads the **sanitized layer** by default; a separate,
operator-authorized mode may read a raw meeting transcript privately to derive
facts but **never commits raw bodies** — that privacy rail is the spine and is
kept verbatim. De-weld swaps the company's owner-routing map for the clone's
roster. *(Specified.)*

### 3d. The genesis engine — the heart (real code in this repo)

**What it is:** the factory that **births a brain from data**. It reads the whole
company corpus, turns it into dated, sourced facts, **resolves conflicts** to a
single current truth, writes the pillars, then **proposes the vision and the
roster** and assembles the review screen.

**Why it exists:** this *is* the product. The "run a brain" half is strong but
common; the "create a brain from raw data" half is the rare, sellable part.

**How it's wired** (the pipeline, end to end):

```
CONNECT → BULK INGEST → CLAIMS → RESOLVE (tier > recency > archive-loser)
        → WRITE PILLARS → DERIVE meta-initiatives → PROPOSE roster
        → BUILD cited wikis → ADVERSARIAL verify → REVIEW SURFACE → you ratify
        → INCREMENTAL CADENCE thereafter
```

The genesis story is **"resurrect, don't rebuild"**: a real ~7,400-line bulk
populator (the recovered upstream populator) already read a company into pillars and was
*archived intact*, not destroyed (history recovery in
[GENESIS-ENGINE-RECOVERY.md](GENESIS-ENGINE-RECOVERY.md)).
So genesis is **~70–80% recoverable**, not built from scratch. The job is to weld
that populator to the newer skills it never had. The two pieces **built in this
repo today** are:

- **The claim resolver** (`genesis/genesis_resolver.py`, from
  [BUILD-SPEC-01](BUILD-SPEC-01-genesis-resolver.md)).
  The old populator, when two facts disagreed, dumped **both** and never picked.
  The resolver decides which is **current**: **your own word beats a primary
  source beats a third-party guess** (`operator > primary > secondary`); newer
  wins within a tier; the loser is **archived, never deleted**; a true
  `disputed` is flagged only when two *same-tier* facts genuinely clash. Pure,
  deterministic, fully tested.
- **The intelligence layer** (`genesis/genesis_pipeline.py`,
  `meta_initiative_deriver.py`, `roster_proposer.py`, `review_surface.py`,
  `genesis_contracts.py`, from
  [BUILD-SPEC-02](BUILD-SPEC-02-genesis-intelligence.md)).
  After the pillars are populated, this **derives meta-initiatives** (1–3 per
  pillar) and **proposes the roster** — the intelligence the old engine never
  had — and assembles the **review packet**. Three rails are **code, not
  aspiration**: (a) **every proposal cites ≥ 1 real anchor or it's dropped**
  (verify-before-relay), (b) **nothing auto-applies** (everything is
  `status="proposed"`), (c) **nothing private leaves to a foreign model** (the
  `EgressGate`, §4).

*(Both are real and green — 42 tests pass. Build-state in
[§ Build state](#build-state).)*

### 3e. Skills shipped *later* (out of v1)

Named in the manifest as later slices, not the first cut: the full code-dispatch
engine (route a coding task to the right model + drive a simulator), the
autonomous overnight back-half, and the two-engine twin build loop.

---

## 4. Tools — the plumbing everything runs on

Skills and agents are *what* and *who*; tools are the **machinery they call**.
Three matter.

### 4a. The connectors — read your sources

**What they are:** the readers that pull raw data out of each source into the
ingest spine.

| Connector | Source | State (per the product map) |
|---|---|---|
| **Cloud** | Email · Calendar · Files · Meetings | Mechanics work; today account-bound to one person, runs in an un-cloneable hosted job. *(~40%.)* |
| **Local** | iMessage · WhatsApp (the phone's chat databases) | Real scraping exists; macOS-only and hardcodes one home path. *(~20%.)* |
| **Code** | A git host (repos/branches) → feeds the R&D pillar | Exists, but hardwired to specific repos; needs "connect *any* repo." *(~15%.)* |
| **Chat** | Slack | Retired in the source company; **needs a full rebuild** (you named it as core). *(0%.)* |

**Why they exist:** they are the **ignition**. No connectors → no corpus → no
brain. Everything downstream is inert without them.

**How they're wired:** each connector feeds the generic **sanitize → normalize →
dedup → route** spine (feature 1), so the rest of the system never sees a
source-specific format and **never sees a raw secret** (the privacy gate strips
credentials/2FA on the way in). *(The spine is ~75% reusable; the connectors are
the least-portable layer and the bulk of the de-weld work.)*

### 4b. The LLM — the reasoning engine

**What it is:** the large language model that does the actual *judgment* —
clustering work into agent domains, drafting pillar facts, deriving
meta-initiatives, writing the plain-English review.

**Why it exists:** the deterministic Python is the *skeleton* (routing,
validation, the rails); the model is the *intelligence* inside it.

**How it's wired — this is the important part:**

- **Model-agnostic by design.** In the genesis code the model is an **injected
  `LLM` protocol** (`complete(system, user, …) -> str`), so *any* provider drops
  in and **tests run against a deterministic stub** (no network in tests). You
  are not locked to one vendor. *(Real, in `genesis_contracts.py`.)*
- **The model never gets the last word on facts.** It can *suggest* (e.g. "here
  are candidate agents / meta-initiatives") but every suggestion must **cite
  real anchors that the Python re-validates** — the model **cannot fabricate an
  anchor or bypass the evidence floor**. If its output is malformed, the parsers
  **fail closed** (no proposals rather than garbage).
- **The egress gate guards every call.** Before any prompt reaches a foreign /
  third-party model, it passes `EgressGate.guard()`, which **classifies the text
  and blocks anything private** — secrets, credentials, PII, contract bodies —
  and treats **unclassifiable text as private (fail closed)**. For a product
  whose whole value is a private corpus, this is a required safety component, not
  a nicety. *(Real, in `genesis_contracts.py`; manifest SDL-23 — one of the
  three NEW first-class components.)*

> **Routing note (for the *build* side, later slices):** when the system
> dispatches a *coding* task to a model, the rule is route-by-output-size —
> small/precise → a fast cheap model; large/broad → a heavy orchestrator;
> borderline → cheap — and **recover the work from disk on a transport drop**
> (the work usually finished even when the channel died). That's the
> `codex-dispatch` doctrine; it's a later slice, not v1.

### 4c. git — the store and the safety layer

**What it is:** version control. It's both the **system of record** (the brain
*is* versioned Markdown) and the substrate the overnight-safety layer reasons
over.

**Why it exists:** two reasons. (1) **Knowledge hygiene** — every fact change is
tracked, stale facts are **superseded-with-archive (never silently deleted)**,
and you can always see what changed and roll back. (2) **Trust** — the
`/close`/`/morning` consolidation **trusts the git merge graph, not what an agent
*says* it did** ("trust-git-not-self-report"); a watchdog watches real
file/commit activity, not a narrator log.

**How it's wired:** the brain lives in a git repo under a `$BRAIN_ROOT` path
(de-welded — no hardcoded home path). `/close` enumerates and consolidates
branches through git; the `claude/**` auto-merge template gathers routine
branches. **One sharp edge, stated plainly:** a "don't push" gate **cannot hold
on a shared `main`** — so the discipline is a **separate branch**, or you state
openly that the gate is unenforceable. *(This is doctrine in
[CLAUDE.md](CLAUDE.md) §3 and a hard
limit in §4.)*

> **What this repo is NOT yet, on git:** it is **not a git repository** today —
> `git init` (plus a `.gitignore` so no token/`.pyc`/`chat.db` ever enters
> history) is an open blocker (B1/B6 in
> [SHIP-GATING-REVIEW.md](SHIP-GATING-REVIEW.md)),
> deliberately left for the operator to own. Don't assume versioning exists.

---

## Build state

What's **real code in this repo** vs **specified-but-still-in-the-private-brain**,
so you know what you can run today.

| Component | In this repo? | State |
|---|---|---|
| **Genesis claim resolver** (`genesis/genesis_resolver.py`) | ✅ Yes | Real, pure, deterministic. Tested (BUILD-SPEC-01 cases). |
| **Genesis intelligence layer** (`genesis_pipeline` · `meta_initiative_deriver` · `roster_proposer` · `review_surface` · `genesis_contracts`) | ✅ Yes | Real. MI-deriver, roster-proposer (≥3-evidence floor), review surface (Type-2), **EgressGate** rail — all built. |
| **Genesis test suite** (`test_genesis_resolver.py` · `test_genesis_intelligence.py`) | ✅ Yes | **42 tests pass** (`/usr/bin/python3 -m pytest -q` in `genesis/` → `42 passed`). Verifies the rails: anchor-or-drop, proposals-only, never-re-propose-base-roster, MIN_EVIDENCE, egress-blocks-private, Type-2 packet, full-corpus mode. |
| **The full ~7,400-line populator** (recovered upstream populator) | ❌ No | Recoverable from the source company's history (archived intact). The resurrect target. See GENESIS-ENGINE-RECOVERY.md. |
| **Ingest spine** (sanitize · normalize · dedup · privacy-gate) | ❌ No | Specified; ~75% reusable; still in the private brain. |
| **Connectors** (cloud / local / code / Slack) | ❌ No | Specified; the least-portable layer (account/path/host-bound; Slack 0%). |
| **The 5 steering skills** (ramble/vision/manifest/morning/pulse) | ❌ No | Specified; exist with strong logic in the private brain, welded to that company. |
| **`/close` · `atomic-decompose` · auto-merge template** | ❌ No | Specified (the NEW components); de-weld plan written. |
| **Self-improvement loop** (fold · skill-deltas · task-proposals) | ❌ No | Specified; ~35% built in the private brain; not ported. |
| **Packaging** (path-resolver · skill namespace · installer · LICENSE · README · root CLAUDE.md) | ⚠️ Partial | `CLAUDE.md` exists (the doctrine kernel). Not yet a git repo; no LICENSE/README/installer. Blockers B1–B12 in SHIP-GATING-REVIEW.md. |

**One-line truth:** the **engine that births a brain has its core beating in
this repo and green**; the **machine that feeds it (connectors, spine) and the
machine that drives it (the 5 skills, the loop)** are designed and proven
elsewhere but **not yet moved in**. The honest ship-readiness number, against
"a stranger clones this and runs it," is low — but the gap is **porting and
de-welding excellent existing parts, not inventing them.**

---

## Where to go next

- **To run the genesis core today:** `cd genesis && /usr/bin/python3 -m pytest -q`
  (this machine's Homebrew Python has a broken `pyexpat`; use the system
  interpreter), then `python genesis_pipeline.py` for the canned end-to-end demo
  and `python genesis_resolver.py` for the resolver demo. (See
  [genesis/README.md](genesis/README.md).)
- **To understand the *why* + sequencing:**
  [GENERALIZED-REPO-MANIFEST.md](GENERALIZED-REPO-MANIFEST.md)
  (components + doctrine + build order).
- **To understand the *product flow*:**
  [PRODUCT-ARCHITECTURE-AND-BUILD-STATE.md](PRODUCT-ARCHITECTURE-AND-BUILD-STATE.md).
- **To see what blocks shipping:**
  [SHIP-GATING-REVIEW.md](SHIP-GATING-REVIEW.md).
- **The rules every clone inherits (how to think/build/talk here):**
  [CLAUDE.md](CLAUDE.md).
