"""ratchet — harvest a recurring miss into a dated CANDIDATE doctrine rule.

THE MECHANISM (plain terms): the one self-improving loop that makes judgment
*compound* instead of repeating the same misses is a dated, append-only,
operator-ratified rules block — fed by real outcomes. The hand-run version only
adds a rule when someone happens to notice. This module is the missing leg: a
deterministic pass that mines the outcome surface for a miss that has recurred
**>= 2x in the same domain**, drafts a candidate rule (one imperative line + a
dated provenance tag citing the >=2 occurrences), and APPENDS it to that
domain's ``## Candidate rules`` block. It NEVER promotes — the operator does.

Why >= 2x: a single one-off is not yet a rule (same bar as the live design
ratchet — a rule is earned by a real, repeated miss). Once is logged and
watched; twice is the trigger.

The outcome surface it mines = the **skill-deltas ledger** (``skill_deltas``).
Each delta already carries its ``recurrence`` count + ``anchors`` (the dated
occurrences) + the domain (its ``skill``), so a delta with ``recurrence >= 2`` is
exactly a recurring miss with its evidence attached. (De-welded from
``ratchet-generalization-v1.md`` §2.3 — the recurring outcome->doctrine pass —
which itself mines reviewer findings + the carry-forward ledger + pulses; here
the ledger is the single, already-structured recurrence surface.)

Rails (CODE, not aspiration), straight from the live doctrine §2.3:
  * **Propose-into-``## Candidate rules``, NEVER promote.** A wrong auto-promotion
    is worse than a missed candidate -> bias to propose-and-wait.
  * **Idempotent.** Re-running proposes nothing new for an already-pending
    candidate (a deterministic candidate id; re-runs are a no-op).
  * **Every candidate cites its real, dated occurrences** (verify-before-relay).
  * **Confined writes** under ``<brain>/loop/ratified-rules/`` (asserted).

No company names / real people / home paths — paths via ``mcs_paths``. Stdlib
only; no network.
"""

from __future__ import annotations

import os
import re
import sys
import time
import uuid
from dataclasses import dataclass

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import mcs_paths  # noqa: E402

import skill_deltas  # noqa: E402  (sibling module; loop/ is on sys.path)

# The minimum recurrence count that earns a candidate rule (the design-ratchet bar).
RECURRENCE_THRESHOLD = 2

# Marker that opens the operator-promotes-only section in every rules block.
_CANDIDATE_HEADER = "## Candidate rules (proposed — awaiting operator ratification)"
_CANDIDATE_NOTE = (
    "<!-- The ratchet pass appends dated, sourced candidates here. The OPERATOR "
    "promotes a candidate into the ratified list above; the pass never promotes. -->"
)


def rules_dir(brain_root=None) -> str:
    """Where the per-domain ratified-rules blocks live."""
    return os.path.join(skill_deltas.loop_root(brain_root), "ratified-rules")


def rules_path(domain: str, brain_root=None) -> str:
    """The rules block for one domain: ``<brain>/loop/ratified-rules/<domain>.md``."""
    return os.path.join(rules_dir(brain_root), f"{_slug(domain)}.md")


def _assert_under_rules_dir(path: str, brain_root) -> None:
    root = os.path.realpath(rules_dir(brain_root))
    real = os.path.realpath(path)
    if not (real == root or real.startswith(root + os.sep)):
        raise RuntimeError(f"Refusing write outside ratified-rules dir: {path}")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-") or "x"


def _now_date() -> str:
    """UTC date stamp (YYYY-MM-DD) for the candidate's provenance tag."""
    return time.strftime("%Y-%m-%d", time.gmtime())


# --------------------------------------------------------------------------- #
# Candidate model                                                              #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Candidate:
    """A proposed rule harvested from a recurring miss. NEVER promoted by this
    module — appended to ``## Candidate rules`` for the operator to ratify."""

    candidate_id: str       # deterministic (idempotent re-runs)
    domain: str             # the rules block it belongs to
    rule_line: str          # the imperative rule + dated provenance tag
    source_delta_id: str
    recurrence: int
    anchors: tuple[str, ...]


def _candidate_id(domain: str, delta_id: str) -> str:
    """Deterministic id from (domain, source delta) so a re-run proposes the SAME
    candidate (idempotency — a re-proposed pending candidate is a no-op)."""
    base = f"{_slug(domain)}|{delta_id}"
    return "cand-" + uuid.uuid5(uuid.NAMESPACE_URL, base).hex[:12]


def _rule_line_for(delta: "skill_deltas.SkillDelta", date: str) -> str:
    """Render the candidate rule line: an imperative directive + a dated
    provenance tag citing the recurring miss and its occurrences (the design-
    block format). The ``what`` is the concrete change; the tag is the evidence."""
    occ = ", ".join(f"`{a}`" for a in delta.anchors) or "(occurrences on file)"
    directive = delta.what.strip().rstrip(".")
    return (
        f"**{directive}.** "
        f"_({date}: recurred {delta.recurrence}x in {delta.skill} — root cause: "
        f"{delta.root_cause.strip().rstrip('.')}; occurrences: {occ}.)_"
        f"  <!-- ratchet: {delta.id} -->"
    )


# --------------------------------------------------------------------------- #
# Harvest                                                                      #
# --------------------------------------------------------------------------- #


def harvest_candidates(brain_root=None) -> list[Candidate]:
    """Find every recurring miss (``recurrence >= RECURRENCE_THRESHOLD``) in the
    skill-deltas ledger and render a candidate rule for each. Pure read — drafts
    nothing to disk; ``run`` does the (confined, idempotent) write.

    A miss only earns a candidate while it is still OPEN: a delta that was already
    APPLIED (its fix is in the skill) or REJECTED (declined) is NOT re-proposed as
    doctrine — only an unaddressed recurring miss should ratchet into a rule.
    """
    date = _now_date()
    out: list[Candidate] = []
    for d in skill_deltas.list_deltas(brain_root):
        if d.recurrence < RECURRENCE_THRESHOLD:
            continue
        if not d.is_open():
            # an addressed (applied) or declined (rejected) miss doesn't ratchet.
            continue
        domain = d.skill or "general"
        out.append(
            Candidate(
                candidate_id=_candidate_id(domain, d.id),
                domain=domain,
                rule_line=_rule_line_for(d, date),
                source_delta_id=d.id,
                recurrence=d.recurrence,
                anchors=d.anchors,
            )
        )
    # deterministic order (domain, then source delta id)
    out.sort(key=lambda c: (_slug(c.domain), c.source_delta_id))
    return out


# --------------------------------------------------------------------------- #
# The rules-block file (append-only; operator promotes from the candidate tail) #
# --------------------------------------------------------------------------- #


def _seed_block(domain: str) -> str:
    """A fresh per-domain rules block: an empty ratified list + the candidate
    section the ratchet writes into (and only the operator promotes out of)."""
    return "\n".join(
        [
            "---",
            f"title: {domain} — operator-ratified rules (append-only)",
            "status: append-only; the ratchet proposes into Candidate rules; "
            "only the operator promotes.",
            "fed_by: the ratchet pass (loop/ratchet.py)",
            "---",
            "",
            f"# {domain} — operator-ratified rules (append-only; this section IS "
            "the feedback loop)",
            "",
            "Each rule traces to a real, repeated miss, so the bar ratchets up "
            "instead of repeating the same misses. Append-only: add rungs; never "
            "silently rewrite. The operator promotes a candidate below into this "
            "numbered list.",
            "",
            "## Ratified rules",
            "",
            "_(none yet — the operator promotes candidates from below.)_",
            "",
            _CANDIDATE_HEADER,
            "",
            _CANDIDATE_NOTE,
            "",
        ]
    ) + "\n"


def _ensure_block(domain: str, brain_root) -> str:
    """Return the path to the domain's rules block, creating a seed if absent."""
    path = rules_path(domain, brain_root)
    _assert_under_rules_dir(path, brain_root)
    if not os.path.isfile(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_seed_block(domain))
    return path


def _already_present(body: str, candidate: Candidate) -> bool:
    """Idempotency check: a candidate already in the file (by its deterministic
    marker comment OR its source-delta ratchet tag) is NOT re-appended."""
    return (
        f"ratchet: {candidate.source_delta_id} " in body
        or f"<!-- ratchet: {candidate.source_delta_id} -->" in body
    )


def run(brain_root=None) -> list[Candidate]:
    """The ratchet pass: harvest recurring misses -> append any NEW candidate rule
    into its domain's ``## Candidate rules`` block. Returns the candidates it
    NEWLY appended (already-present ones are skipped — idempotent).

    NEVER promotes a candidate into the ratified list. NEVER touches the numbered
    ratified rules. Writes only the candidate tail, only under the confined
    ratified-rules dir.
    """
    appended: list[Candidate] = []
    for cand in harvest_candidates(brain_root):
        path = _ensure_block(cand.domain, brain_root)
        with open(path, "r", encoding="utf-8") as fh:
            body = fh.read()
        if _already_present(body, cand):
            continue  # idempotent: don't re-propose a pending candidate
        body = _append_candidate(body, cand)
        _assert_under_rules_dir(path, brain_root)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        appended.append(cand)
    return appended


def _append_candidate(body: str, candidate: Candidate) -> str:
    """Insert the candidate's rule line into the ``## Candidate rules`` section,
    after the note line. If the section is missing (an externally-seeded block),
    append a fresh section at the end. Append-only — never edits existing lines."""
    line = f"- {candidate.rule_line}"
    if _CANDIDATE_HEADER in body:
        # append at the very end of the file (the candidate section is last by
        # construction). Append-only: we only add a line, never rewrite.
        if not body.endswith("\n"):
            body += "\n"
        return body + line + "\n"
    # no candidate section -> add one (still append-only at file end)
    if not body.endswith("\n"):
        body += "\n"
    return body + "\n" + _CANDIDATE_HEADER + "\n\n" + _CANDIDATE_NOTE + "\n\n" + line + "\n"


__all__ = [
    "RECURRENCE_THRESHOLD",
    "Candidate",
    "rules_dir", "rules_path",
    "harvest_candidates", "run",
]
