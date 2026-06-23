# Security Policy

## In plain terms

MorningCoffeeSip handles a company's most sensitive raw data — emails, message
threads, files, calendars. Security is not a side concern here; it is the
product. If you find a way to make this tool leak private data, send something it
shouldn't, or expose a secret, **please tell us privately first** so it can be
fixed before it's public. Do not open a public issue for a vulnerability.

## Reporting a vulnerability

**Do not report security issues through public GitHub issues, discussions, or
pull requests.**

Instead, use GitHub's private vulnerability reporting:

1. Go to the repository's **Security** tab → **Report a vulnerability** (GitHub
   Private Vulnerability Reporting). This opens a private advisory only the
   maintainers can see.

If that is unavailable to you, open a regular issue titled only
`security: request a private contact` with **no details** of the vulnerability,
and a maintainer will open a private channel.

Please include, in the private report:

- what the issue is and the impact you think it has,
- the steps to reproduce it (a minimal proof-of-concept is ideal),
- the version / commit you tested, and
- any suggested fix, if you have one.

We aim to acknowledge a report within a few days and to keep you updated as we
work on a fix. Please give us a reasonable window to remediate before any public
disclosure — we will credit you (if you want credit) when the fix ships.

## What is in scope

Because the entire value of this project is keeping a private corpus private, the
issues we care most about are:

- **Private-data egress** — any path by which raw private content reaches a
  foreign / third-party model. The data-boundary gate
  ([`mcs_egress.py`](mcs_egress.py)) is meant to fail *closed*; a way around it,
  or an input it misclassifies as safe, is a high-severity bug.
- **Secret / credential leakage** — any way a token, key, password, or
  credential ends up in a committed file, a log, the brain, or chat-visible
  output.
- **Unsanitized ingest** — private content (2FA codes, secrets, PII) surviving
  the sanitize step and landing in the brain.
- **Unauthorized external action** — the system sending, filing, posting,
  purchasing, or committing/pushing without the operator's explicit approval
  (the hard limits in [`CLAUDE.md`](CLAUDE.md)).
- **Destructive operations** — any path that deletes, force-pushes, or rewrites
  history without explicit approval.

## What is out of scope

- Vulnerabilities in third-party LLM providers, OAuth providers, or your
  operating system — report those to the respective vendors.
- Issues that require an attacker to already have full local access to the
  operator's machine and their unlocked credentials (this is a single-operator,
  runs-on-your-own-machine tool by design).

## A note on the threat model

This is a **single-operator, self-hosted** tool: you clone it, you run it on your
machine, against your own data. There is no shared server, no multi-tenant
surface, nothing phones home. The primary risks are therefore (1) data leaving to
a foreign model and (2) secrets entering git history — which is exactly what the
egress gate, the sanitize spine, the [`.gitignore`](.gitignore), and the secrets
scan ([`scripts/scan-secrets.sh`](scripts/scan-secrets.sh)) exist to prevent. If
you find a gap in any of those, that's the report we most want.
