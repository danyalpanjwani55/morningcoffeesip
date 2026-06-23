# Contributing to MorningCoffeeSip

Thanks for your interest. This is an early, in-progress build — the genesis
engine's brain runs and is tested today; most of the surrounding system is
specified but not yet ported (see the [README](README.md) "What works today vs.
what is specified" table). That means there is a lot of well-scoped work to pick
up, and the build docs tell you exactly what each piece should do.

## In plain terms

Read [`CLAUDE.md`](CLAUDE.md) first — it is the law of this repo (how to think,
how to build, how to talk, and the hard limits nobody crosses). Then pick work
from the build docs, make a small change, prove it with a test, and open a pull
request. The bar is: every changed line traces to a real need, and "done" is a
test that passes — not "looks right."

## Before you start — read these, in order

1. **[`CLAUDE.md`](CLAUDE.md)** — the doctrine, the coding rules (Musk's
   algorithm, then Karpathy's rules), and the four hard limits. Non-negotiable.
2. **[`docs/SYSTEM.md`](docs/SYSTEM.md)** — the component map: every feature,
   agent, skill, and tool, and the percent-built state of each piece (where the
   open work is).
3. **[`docs/SETUP.md`](docs/SETUP.md)** — how to stand the system up and the
   genesis engine flow, for the part you're touching.

## Development setup

You need **Python 3.9 or newer**. The product code is pure standard library —
there is nothing to `pip install` to *run* it. The only dev dependency is
`pytest`, to run the test suite.

```sh
git clone <this-repo-url>
cd morningcoffeesip

# (optional) a virtual environment for the test tool
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install pytest

# Run the whole suite — this is the bar your change must keep green.
python3 -m pytest -q

# See the genesis engine run end-to-end on built-in sample data.
python3 genesis/genesis_pipeline.py
```

> Note: on some systems the newest Homebrew `python3` has a broken `pyexpat`
> that stops `pytest` from importing. `/usr/bin/python3` (the system Python) is
> a reliable fallback: `/usr/bin/python3 -m pytest -q`.

## How we work here (the short version of `CLAUDE.md`)

- **Question, then delete, then simplify — in that order.** The cheapest change
  is the one you don't make. Before adding code, ask whether the requirement is
  real and whether something can be removed instead.
- **Simplest thing that works.** No abstractions for one-time code, no
  flexibility nobody asked for, no error-handling for cases that can't happen.
  If 200 lines could be 50, write the 50.
- **Touch only what the task needs.** No drive-by refactors of code that isn't
  broken. Flag dead code; don't delete it unless that's the task.
- **Define "done," then loop until it's true.** Turn the task into a checkable
  bar *before* you write code: "fix the bug" → "a test that fails, then make it
  pass."
- **Two document types.** Anything you write is either FOR-AI (dense,
  source-anchored) or FOR-THE-OPERATOR (plain-English, opens with "In plain
  terms"). Never both.

## The hard limits (apply to contributors and to the agents alike)

These are enforced in spirit and, where possible, in code. A PR that crosses any
of them will not be merged:

1. **No external sends** — nothing the outside world can see without explicit
   approval.
2. **No moving money**, and **no touching credentials or secrets.**
3. **No private data, secrets, or credentials** in committed files, logs, or
   output. Sanitize on the way in; classify on the way out. (Run the secrets
   scan below before you push.)
4. **No destructive operations** — no force-push, no history rewrite, no
   branch/tag deletion, no mass delete.

## Before you open a PR — the checklist

- [ ] `python3 -m pytest -q` is **green** (and you added a test for any new
      behavior — a change without a test is not done).
- [ ] No secrets, tokens, real names, private company data, or machine-specific
      home paths in your diff. Run the scan:
      ```sh
      ./scripts/scan-secrets.sh
      ```
- [ ] The code is **stdlib-only** unless the change is explicitly about adding a
      dependency (and if it is, that's a discussion to open first, not a
      surprise in a PR).
- [ ] Every changed line traces back to the issue/spec you're addressing — no
      unrelated reformatting or "improvements."
- [ ] If you generated any of the change with an AI agent, you still own it:
      you've read every line and you can defend it.

## Pull requests

- Branch off, make the change, open a PR with a clear description of **what** and
  **why** (the why as a real-world consequence, not just mechanism).
- Keep PRs small and single-purpose. A reviewer should be able to hold the whole
  change in their head.
- If you hit an irreversible or ambiguous fork, **stop and ask in the PR** rather
  than guessing. A truthful "I stopped here and here's why" beats a confident
  wrong move.

## Reporting bugs and proposing features

- **Security vulnerabilities:** do **not** open a public issue. Follow
  [`SECURITY.md`](SECURITY.md) for private disclosure.
- **Bugs / features:** open an issue describing what you expected, what
  happened, and (for bugs) the smallest reproduction you can.

By contributing, you agree your contributions are licensed under the project's
[Apache License 2.0](LICENSE).
