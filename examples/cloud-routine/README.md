# Cloud-routine templates — the schedule half, ready to adapt

*Two **templates** you copy and fill in to stand up the scheduled cloud half of the brain (email /
Drive / calendar). They are the concrete companions to the recipe in
[`../../docs/CLOUD-ROUTINE.md`](../../docs/CLOUD-ROUTINE.md). Nothing here runs by itself — they are
fill-in-the-blank starting points, deliberately placed under `examples/` so cloning this repo never
activates a schedule or a workflow on its own.*

---

## In plain terms

The cloud half of the brain — the part that reads your **email, shared files, and calendar** — runs
as a **scheduled cloud agent** (a "routine": an AI assistant set to run on a clock, read-only, like a
cron job but for an assistant). [`../../docs/CLOUD-ROUTINE.md`](../../docs/CLOUD-ROUTINE.md) is the
recipe; **this folder is the two fill-in-the-blank files that recipe points at**, so you're not
starting from a blank page:

| File | What it is | You do with it |
|---|---|---|
| [`routine.sample.json`](routine.sample.json) | A **checklist of every choice** a routine needs — the schedule, which read-only connectors to attach, the tools to deny, the review branch, and a pointer to the standing prompt. | Copy it, fill in the `TODO`s, and transcribe it into your assistant platform's routine UI. It is **not** consumed verbatim — it's the portable list so you don't forget a field (especially the read-only scope and the deny-list). |
| [`github-action-automerge.sample.yml`](github-action-automerge.sample.yml) | A **GitHub Action** that lands each routine run's branch onto `main` safely (non-force, abort-on-conflict, test-gated). | Copy it into `.github/workflows/` **in your own fork** to turn it on. It lives here (under `examples/`) precisely so it does **not** auto-run in this repo. |

**Why both, not just the routine.** The routine writes its proposals to its **own** branch — never
your `main` — and you merge at the `/morning` gate. But the assistant platform reassigns that branch
a new throwaway name on roughly every run, and a routine on a shared branch **cannot be technically
stopped from pushing by a rule in a file** (the `trust-git-not-self-report` rule). So the safe design
is: routine writes to a review branch → the Action lands those branches onto `main` **only** if they
merge cleanly and pass tests. The Action is the guardrail that makes "let it run while I sleep" safe
to wake up to.

**The one bug worth knowing about (it's why the Action matches `claude/**`).** A naive version that
pinned **one** branch name silently stranded ~95 routine runs over two days in a real incident — the
next morning gate then ran on stale data. The template matches **all** routine branches
(`claude/**`) so no run is ever stranded. Keep that matcher.

---

## How to use them (the 4 steps)

1. **Read the recipe first:** [`../../docs/CLOUD-ROUTINE.md`](../../docs/CLOUD-ROUTINE.md). It has the
   turnkey connect-your-accounts flow and the **paste-in standing prompt** (the routine's job
   description). These templates assume you've read it.
2. **Fill in `routine.sample.json`** — your fork URL, your schedule, the connectors you want
   (read-only), and confirm the deny-list. Then create the routine on your assistant platform from it
   and paste in the standing prompt from the recipe.
3. **Turn on the Action** — copy `github-action-automerge.sample.yml` into `.github/workflows/` in
   your fork, and edit the bot identity + the code-path glob/test command to match your tree (the
   header in the file lists exactly what to change).
4. **Let the routine run once, then run the local agent.** The routine writes
   `sources/sent-correspondents.txt` (the list the local iMessage/WhatsApp lanes filter against), so
   run it **before** `python -m ingest.local.sync` the first time — see
   [`../../docs/INGEST-ARCHITECTURE.md`](../../docs/INGEST-ARCHITECTURE.md).

---

## The rails these templates bake in (do not weaken)

Both files restate the hard limits from [`../../CLAUDE.md`](../../CLAUDE.md) §4 for this specific job:

- **Read-only on every connector** — never send, reply, change a calendar, move/delete a file, move
  money, or touch a credential. The routine config **denies** the mutate tools explicitly, because a
  routine often inherits them even when its connectors are read-only.
- **No raw secrets ever leave** — the routine commits only *derived* notes; never a raw private body,
  a password, or a one-time code. **There are no tokens or account ids in these files** — those live
  in your assistant account, never in this repo. If you ever start to paste a key here, stop: a secret
  in a public repo is an irreversible leak.
- **Proposals-only** — the routine writes to a review branch; the Action lands it only on a clean,
  test-passing merge; **you** bless it at `/morning`. Nothing is sent, nothing auto-applies.
- **Idle = inert** — nothing new since the last run means nothing done.

---

## A note on honesty (what's a template vs. what's proven code)

These two files are **templates** — a job specification and a workflow you adapt — not shipped,
unit-tested product code, and this README says so plainly rather than implying a green check it
doesn't have. What *is* built and tested in code is the part the routine reuses: the
**correspondent-harvest + spam-drop** email logic
([`../../ingest/adapters/email_source.py`](../../ingest/adapters/email_source.py)) and the
**sanitize / egress gate** every note passes through
([`../../ingest/sanitize.py`](../../ingest/sanitize.py),
[`../../mcs_egress.py`](../../mcs_egress.py)). The templates' job is to drive that proven spine from
the cloud side under the rails above — not to reinvent it.

---

## Where to go next

- **The full recipe + paste-in prompt:** [`../../docs/CLOUD-ROUTINE.md`](../../docs/CLOUD-ROUTINE.md).
- **The local↔cloud split:** [`../../docs/INGEST-ARCHITECTURE.md`](../../docs/INGEST-ARCHITECTURE.md).
- **Point the on-ramp at your data:** [`../../docs/CONNECT.md`](../../docs/CONNECT.md).
- **The bundled fictional corpus (the other `examples/` folder):**
  [`../sample-company/README.md`](../sample-company/README.md).
