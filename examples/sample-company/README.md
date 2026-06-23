# Sample company corpus — "Aurora Tea Co." (fictional)

A tiny, **entirely made-up** multi-source corpus so a stranger can watch the
on-ramp work end-to-end without connecting any real data. Nothing here is a real
person, a real company, or a real address — it is a fixture, by design.

`run.py` (at the repo root) points the ingest spine at this folder, runs genesis,
prints the review packet, and (on ratify) builds the cited agent wikis.

## What's in here

- `notes/` — a handful of `.md`/`.txt` founder notes (decisions, a standup, a
  pricing review, an ops note, a hiring thread). The `LocalFilesAdapter` reads
  these.
- `mail/` — a handful of `.eml` messages (a supplier thread, a customer-support
  escalation, a partnership reply). The `EmailAdapter` reads these.

## What it's built to exercise (so the demo is honest, not a happy-path toy)

- **Pillars populate** — notes/mail mention product, pricing/GTM, hiring/people,
  and ops, so the keyword router fills several pillars.
- **A conflict resolves** — two sources disagree on `launch_date` (a founder
  decision vs an older draft); the resolver keeps one current value and archives
  the loser (operator word > older note).
- **A roster agent is proposed from real evidence** — the **customer-support**
  thread recurs across ≥3 distinct sources, so the roster proposer clears its
  `≥3 distinct anchored signals` floor and proposes a support specialist BEYOND
  the base roster. (Sourcing/supplier also recurs — a second candidate.)
- **The privacy gate visibly drops a record** — one note deliberately contains a
  fake secret (an API key) and one a fake SSN. The sanitizer drops them with a
  reason code; they never become events, never reach the model, never appear in
  a wiki. You'll see them counted as `dropped_private` in the run summary.

All values (prices, dates, names) are invented for the fixture.
