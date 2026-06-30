"""skill_deltas — the close-the-loop ledger (de-welded from the live brain).

THE PROBLEM this solves (plain terms): every time a session or an agent decides
"this skill should change," that idea used to get written into a note and then
**die there** — nothing tracked whether it ever got applied. The notebooks
learned; the skills themselves did not. This module is the mechanism that makes
a proposed skill-change get ONE durable row and stay tracked until it is either
APPLIED or REJECTED — nothing rots silently.

THE LOOP (operator-gated, reversible, archive-don't-delete):

    capture(lesson)            -> a routed PROPOSAL, status "proposed"
        |                         (NEVER auto-applies; capture only proposes)
        v
    [the morning gate — the operator does ONE pass]
        |
        +-- apply(id)          -> archives a PRE-IMAGE of the target skill file
        |                         (the revert point), then flips status "applied"
        +-- reject(id, why)    -> status "rejected" + reason (stops it re-surfacing)
        |
    revert(id)                 -> ONE command: restore the target file from its
                                  archived pre-image; flips status back "proposed".
                                  The pre-image is archived, never deleted — so a
                                  bad apply is one revert away.

Storage = an **append-only JSONL ledger** (the machine substrate, one event per
line, never rewritten in place) + a deterministic **Markdown render** (the
operator-facing view). Status is the fold of the event stream, newest wins.

De-welded from:
  * ops/exchange/skill-deltas-ledger.md   (the row format + the OPEN/APPLIED/
    REJECTED/SUPERSEDED lifecycle + the recurrence-escalates-never-duplicates rule)
  * session-writeback                      (supersede-with-archive, never delete)
  * the opus-relegation review regime      (proposals-only, nothing auto-applies)

No company names, no real people, no home paths — all paths resolve through
``mcs_paths`` and the ledger lives under the brain root. Stdlib only; no network.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

# The reusable repo-root modules (mcs_paths) live one level up; the loop package
# is run with ``loop/`` on sys.path (flat imports, like genesis), so put the repo
# root on sys.path. This shim exists only because the repo isn't yet a packaged
# distribution (mirrors genesis_contracts' shim); once it is, this can go.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import mcs_paths  # noqa: E402

# --------------------------------------------------------------------------- #
# Lifecycle constants                                                          #
# --------------------------------------------------------------------------- #

PROPOSED = "proposed"      # captured, unresolved (the only status capture can set)
APPLIED = "applied"        # folded into the skill + a pre-image archived
REJECTED = "rejected"      # declined, reason recorded
SUPERSEDED = "superseded"  # folded into a later/bigger delta

_OPEN_STATUS = PROPOSED
_TERMINAL = (APPLIED, REJECTED, SUPERSEDED)

# Priority ladder (ordered low -> high, so an escalation can bump up one rung).
_PRIORITY_LADDER = ("low", "med", "high")


# --------------------------------------------------------------------------- #
# Path resolution (all under the brain root; nothing escapes it)               #
# --------------------------------------------------------------------------- #


def loop_root(brain_root: str | os.PathLike[str] | None = None) -> str:
    """The loop's home under the brain root: ``<brain>/loop``."""
    return os.path.join(str(mcs_paths.brain_root(brain_root)), "loop")


def ledger_path(brain_root: str | os.PathLike[str] | None = None) -> str:
    """The append-only event log (JSONL)."""
    return os.path.join(loop_root(brain_root), "skill-deltas-ledger.jsonl")


def ledger_md_path(brain_root: str | os.PathLike[str] | None = None) -> str:
    """The rendered operator-facing Markdown view (regenerated; not the source)."""
    return os.path.join(loop_root(brain_root), "skill-deltas-ledger.md")


def preimage_dir(brain_root: str | os.PathLike[str] | None = None) -> str:
    """Where pre-images (the revert points) are archived — never deleted."""
    return os.path.join(loop_root(brain_root), "preimages")


def _assert_under_loop_root(path: str, brain_root) -> None:
    """Refuse any write whose resolved path escapes the loop root (defense in
    depth: a bad id/slug with ``../`` can never write outside ``<brain>/loop``)."""
    root = os.path.realpath(loop_root(brain_root))
    real = os.path.realpath(path)
    if not (real == root or real.startswith(root + os.sep)):
        raise RuntimeError(f"Refusing write outside loop root: {path}")


# --------------------------------------------------------------------------- #
# The delta record (one logical skill-change; folded from its event stream)    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SkillDelta:
    """One logical proposed skill-change, as currently folded from the ledger.

    ``skill`` + ``root_cause`` together are the recurrence key — a second lesson
    with the same pair ESCALATES this row (bumps priority + recurrence, appends
    the anchor); it never files a duplicate. ``status`` is operator-gated: only
    a separate apply/reject/revert op moves it off ``proposed``.
    """

    id: str
    skill: str                       # the skill the change targets (routed owner)
    root_cause: str                  # the recurrence key (with skill)
    what: str                        # the concrete named change
    why: str                         # the real-world cost of NOT doing it
    owner: str                       # who drafts the diff (the specific owner)
    priority: str = "med"
    status: str = PROPOSED
    recurrence: int = 1
    anchors: tuple[str, ...] = ()    # source anchors (>=1; each occurrence adds one)
    born: str = ""                   # ISO timestamp of first capture
    resolution: str = ""             # reject reason / registry id / supersede target
    preimage: str = ""               # archived pre-image path (set on apply)

    def is_open(self) -> bool:
        return self.status == _OPEN_STATUS

    def recurrence_key(self) -> tuple[str, str]:
        return (_norm_key(self.skill), _norm_key(self.root_cause))


def _norm_key(s: str) -> str:
    """Normalize a recurrence-key component: lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _bump_priority(p: str) -> str:
    """Escalate one rung up the ladder (high is the ceiling)."""
    p = str(p).strip().lower()
    try:
        i = _PRIORITY_LADDER.index(p)
    except ValueError:
        return "med"
    return _PRIORITY_LADDER[min(i + 1, len(_PRIORITY_LADDER) - 1)]


# --------------------------------------------------------------------------- #
# The append-only event log                                                    #
# --------------------------------------------------------------------------- #


def _now() -> str:
    """ISO-8601 UTC, deterministic format (overridable by tests via patching)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _append_event(event: dict[str, Any], *, brain_root=None) -> None:
    """Append ONE event line to the JSONL ledger. Never rewrites existing lines
    (append-only is the integrity guarantee)."""
    path = ledger_path(brain_root)
    _assert_under_loop_root(path, brain_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")


def _read_events(brain_root=None) -> list[dict[str, Any]]:
    """Read every event line in order (oldest first)."""
    path = ledger_path(brain_root)
    if not os.path.isfile(path):
        return []
    out: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _fold(brain_root=None) -> dict[str, SkillDelta]:
    """Fold the event stream into the current state of each delta (newest wins).

    Event kinds:
      * ``capture``   — born a new delta (or, when it escalates, a new occurrence)
      * ``escalate``  — a recurrence on an existing id (priority++/recurrence++)
      * ``apply``     — flip to applied + record the pre-image path
      * ``reject``    — flip to rejected + record the reason
      * ``supersede`` — flip to superseded + name the target
      * ``revert``    — flip applied -> proposed (the pre-image was restored)
    """
    state: dict[str, dict[str, Any]] = {}
    for ev in _read_events(brain_root):
        kind = ev.get("kind")
        did = ev.get("id")
        if not did:
            continue
        if kind == "capture":
            state[did] = {
                "id": did,
                "skill": ev.get("skill", ""),
                "root_cause": ev.get("root_cause", ""),
                "what": ev.get("what", ""),
                "why": ev.get("why", ""),
                "owner": ev.get("owner", ""),
                "priority": ev.get("priority", "med"),
                "status": PROPOSED,
                "recurrence": int(ev.get("recurrence", 1)),
                "anchors": list(ev.get("anchors", [])),
                "born": ev.get("at", ""),
                "resolution": "",
                "preimage": "",
            }
        elif did not in state:
            # an op for an unknown id is ignored (can't resolve what isn't captured)
            continue
        elif kind == "escalate":
            cur = state[did]
            cur["recurrence"] = int(cur.get("recurrence", 1)) + 1
            cur["priority"] = _bump_priority(cur.get("priority", "med"))
            anc = ev.get("anchor")
            if anc and anc not in cur["anchors"]:
                cur["anchors"].append(anc)
        elif kind == "apply":
            cur = state[did]
            cur["status"] = APPLIED
            cur["preimage"] = ev.get("preimage", "")
            cur["resolution"] = ev.get("resolution", "applied")
        elif kind == "reject":
            cur = state[did]
            cur["status"] = REJECTED
            cur["resolution"] = ev.get("why", "")
        elif kind == "supersede":
            cur = state[did]
            cur["status"] = SUPERSEDED
            cur["resolution"] = f"superseded by {ev.get('by', '?')}"
        elif kind == "revert":
            cur = state[did]
            # back to proposed — the applied edit was rolled back from the pre-image
            cur["status"] = PROPOSED
            cur["resolution"] = ""
            cur["preimage"] = ""

    return {did: SkillDelta(**_coerce(d)) for did, d in state.items()}


def _coerce(d: dict[str, Any]) -> dict[str, Any]:
    """Coerce a folded dict into SkillDelta kwargs (anchors -> tuple)."""
    out = dict(d)
    out["anchors"] = tuple(out.get("anchors", []))
    return out


# --------------------------------------------------------------------------- #
# Public read API                                                              #
# --------------------------------------------------------------------------- #


def list_deltas(brain_root=None) -> list[SkillDelta]:
    """All deltas in deterministic (born, id) order."""
    deltas = list(_fold(brain_root).values())
    deltas.sort(key=lambda x: (x.born, x.id))
    return deltas


def open_deltas(brain_root=None) -> list[SkillDelta]:
    """Only the unresolved (``proposed``) deltas — what the morning gate shows.

    Highest priority first, then most-recurrent, then born order (the operator
    sees the loudest signals at the top)."""
    rank = {p: i for i, p in enumerate(_PRIORITY_LADDER)}
    opens = [d for d in _fold(brain_root).values() if d.is_open()]
    opens.sort(key=lambda x: (-rank.get(str(x.priority).lower(), 0),
                              -x.recurrence, x.born, x.id))
    return opens


def get_delta(delta_id: str, brain_root=None) -> SkillDelta | None:
    return _fold(brain_root).get(delta_id)


def _find_by_recurrence_key(skill: str, root_cause: str, brain_root=None
                            ) -> SkillDelta | None:
    """Find any existing delta (open OR resolved) on the same skill + root cause —
    the recurrence check. A resolved-then-recurring match is a LOUDER signal, so
    it is still returned (the caller re-opens + escalates it)."""
    key = (_norm_key(skill), _norm_key(root_cause))
    for d in _fold(brain_root).values():
        if d.recurrence_key() == key:
            return d
    return None


# --------------------------------------------------------------------------- #
# CAPTURE — born a proposal (the only op that creates; never auto-applies)     #
# --------------------------------------------------------------------------- #


def capture(
    *,
    skill: str,
    root_cause: str,
    what: str,
    why: str,
    owner: str,
    anchor: str,
    priority: str = "med",
    brain_root=None,
) -> SkillDelta:
    """Capture a lesson as a routed PROPOSAL (status ``proposed``).

    Recurrence rule (de-welded verbatim): BEFORE filing a new row, look for a
    prior delta on the **same skill + same root cause**. If one exists (open OR
    already resolved), do NOT duplicate — ESCALATE it: bump priority one rung,
    increment recurrence, append this anchor (and re-open it if it had been
    resolved — a recurrence after a fix means the fix did not hold). Only a
    genuinely new (skill, root_cause) pair gets a fresh row.

    This NEVER applies anything. The returned delta is ``proposed`` and stays so
    until a separate ``apply``/``reject`` op at the morning gate.
    """
    if not anchor or not str(anchor).strip():
        # verify-before-relay: a proposal with no source anchor is invalid.
        raise ValueError("capture requires a non-empty source anchor.")

    existing = _find_by_recurrence_key(skill, root_cause, brain_root)
    if existing is not None:
        # ESCALATE rather than duplicate. If it was resolved, re-open it loud.
        _append_event(
            {
                "kind": "escalate",
                "id": existing.id,
                "anchor": anchor,
                "at": _now(),
                "note": ("recurrence after a resolved fix — the fix did not hold"
                         if not existing.is_open() else "recurrence"),
            },
            brain_root=brain_root,
        )
        if not existing.is_open():
            # re-open: a terminal delta that recurs goes back to proposed.
            _append_event(
                {"kind": "revert", "id": existing.id, "at": _now(),
                 "note": "re-opened by recurrence"},
                brain_root=brain_root,
            )
        render_markdown(brain_root)
        return get_delta(existing.id, brain_root)  # type: ignore[return-value]

    delta_id = _new_id(skill, root_cause)
    _append_event(
        {
            "kind": "capture",
            "id": delta_id,
            "skill": skill,
            "root_cause": root_cause,
            "what": what,
            "why": why,
            "owner": owner,
            "priority": str(priority).strip().lower() or "med",
            "recurrence": 1,
            "anchors": [anchor],
            "at": _now(),
        },
        brain_root=brain_root,
    )
    render_markdown(brain_root)
    return get_delta(delta_id, brain_root)  # type: ignore[return-value]


def capture_correction(
    *,
    skill: str,
    root_cause: str,
    what: str,
    why: str,
    owner: str,
    anchor: str = "",
    priority: str = "med",
    brain_root=None,
) -> SkillDelta:
    """File a ``proposed`` skill-delta the INSTANT the operator (or another agent)
    corrects a substantive error, in-conversation — without waiting for close-out.

    This is the one-call convenience entrypoint pulse Part 4c points at ("an
    unfiled callout is a lost callout"): the moment a correction lands mid-session,
    an agent calls this and the lesson is tracked from that instant. It is a THIN
    wrapper over ``capture()`` — same recurrence/escalation behavior, same
    proposed-only / operator-gated guarantee, nothing new. The only convenience:
    a mid-conversation correction has no pulse/handoff slug yet, so ``anchor``
    defaults to an ``in-conversation-correction`` marker (capture() still requires
    a non-empty anchor; this just supplies a sensible default for the in-the-moment
    case). Pass a real anchor when you have one.
    """
    return capture(
        skill=skill,
        root_cause=root_cause,
        what=what,
        why=why,
        owner=owner,
        anchor=str(anchor).strip() or "in-conversation-correction",
        priority=priority,
        brain_root=brain_root,
    )


def _new_id(skill: str, root_cause: str) -> str:
    """A short, stable-ish id derived from the recurrence key + a uuid tail
    (the tail keeps two captures that share a normalized key but are filed in
    separate runs from colliding before the recurrence check can dedupe — in
    practice the recurrence check fires first, so this is belt-and-suspenders)."""
    base = f"{_norm_key(skill)}|{_norm_key(root_cause)}"
    tail = uuid.uuid5(uuid.NAMESPACE_URL, base + "|" + uuid.uuid4().hex).hex[:8]
    slug = re.sub(r"[^a-z0-9]+", "-", _norm_key(skill)).strip("-")[:24] or "skill"
    return f"sd-{slug}-{tail}"


# --------------------------------------------------------------------------- #
# APPLY — operator one-pass: archive a pre-image, then flip to applied         #
# --------------------------------------------------------------------------- #


def apply(
    delta_id: str,
    target_file: str,
    *,
    registry_id: str = "",
    brain_root=None,
) -> SkillDelta:
    """APPLY a delta: archive a PRE-IMAGE of ``target_file`` (the revert point),
    then flip the delta to ``applied``.

    The pre-image is the whole revert mechanism: a verbatim copy of the target
    skill file BEFORE the edit, archived under ``<brain>/loop/preimages/`` and
    NEVER deleted — so ``revert(id)`` can restore it with one command. This
    function archives the pre-image; the actual edit to ``target_file`` is the
    operator/owner's to make (this op records that an apply happened + where the
    pre-image is, exactly as the live ledger pairs an APPLIED row with an
    archived pre-image).

    Raises:
        ValueError: the delta doesn't exist, or isn't ``proposed`` (you can't
            apply something already applied/rejected — that would lose the
            audit trail; re-open via a recurrence capture instead).
        FileNotFoundError: ``target_file`` doesn't exist (nothing to pre-image).
    """
    delta = get_delta(delta_id, brain_root)
    if delta is None:
        raise ValueError(f"no such delta: {delta_id!r}")
    if delta.status != PROPOSED:
        raise ValueError(
            f"refusing to apply delta {delta_id!r}: status is {delta.status!r}, "
            f"expected {PROPOSED!r} (apply only an open delta)."
        )
    if not os.path.isfile(target_file):
        raise FileNotFoundError(
            f"target file does not exist (nothing to pre-image): {target_file}"
        )

    preimage = _archive_preimage(delta_id, target_file, brain_root)
    _append_event(
        {
            "kind": "apply",
            "id": delta_id,
            "target": os.path.abspath(target_file),
            "preimage": preimage,
            "resolution": registry_id or "applied",
            "at": _now(),
        },
        brain_root=brain_root,
    )
    render_markdown(brain_root)
    return get_delta(delta_id, brain_root)  # type: ignore[return-value]


def _archive_preimage(delta_id: str, target_file: str, brain_root) -> str:
    """Copy ``target_file`` verbatim into the pre-image archive and return the
    archived path. The filename records the delta id + the target's basename so a
    human can read the archive. Archive-don't-delete: a prior pre-image for the
    same id is never overwritten — a suffix is added."""
    pdir = preimage_dir(brain_root)
    _assert_under_loop_root(pdir, brain_root)
    os.makedirs(pdir, exist_ok=True)
    base = os.path.basename(target_file)
    dest = os.path.join(pdir, f"{delta_id}__{base}")
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(pdir, f"{delta_id}__{base}.{n}")
        n += 1
    _assert_under_loop_root(dest, brain_root)
    shutil.copy2(target_file, dest)
    return dest


# --------------------------------------------------------------------------- #
# REJECT — declined, reason recorded (a real outcome; stops re-surfacing)      #
# --------------------------------------------------------------------------- #


def reject(delta_id: str, why: str, *, brain_root=None) -> SkillDelta:
    """REJECT a delta with a one-line reason. A rejection is a real outcome — it
    stops the idea re-surfacing at the next gate. Requires a reason (a silent
    reject loses the why)."""
    delta = get_delta(delta_id, brain_root)
    if delta is None:
        raise ValueError(f"no such delta: {delta_id!r}")
    if delta.status != PROPOSED:
        raise ValueError(
            f"refusing to reject delta {delta_id!r}: status is {delta.status!r}, "
            f"expected {PROPOSED!r}."
        )
    if not why or not str(why).strip():
        raise ValueError("reject requires a one-line reason.")
    _append_event(
        {"kind": "reject", "id": delta_id, "why": str(why).strip(), "at": _now()},
        brain_root=brain_root,
    )
    render_markdown(brain_root)
    return get_delta(delta_id, brain_root)  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# REVERT — one command, restore from the archived pre-image                    #
# --------------------------------------------------------------------------- #


def revert(delta_id: str, *, brain_root=None) -> SkillDelta:
    """REVERT an applied delta with ONE command: restore the target file from its
    archived pre-image, then flip the delta back to ``proposed``.

    Archive-don't-delete: the pre-image itself is NOT deleted on revert (so a
    revert is itself reversible — you can re-apply). A bad apply is one
    ``revert`` away.

    Raises:
        ValueError: the delta doesn't exist or isn't ``applied``.
        FileNotFoundError: the archived pre-image is missing (can't restore).
    """
    delta = get_delta(delta_id, brain_root)
    if delta is None:
        raise ValueError(f"no such delta: {delta_id!r}")
    if delta.status != APPLIED:
        raise ValueError(
            f"refusing to revert delta {delta_id!r}: status is {delta.status!r}, "
            f"expected {APPLIED!r} (only an applied delta can be reverted)."
        )
    if not delta.preimage or not os.path.isfile(delta.preimage):
        raise FileNotFoundError(
            f"pre-image missing for delta {delta_id!r}: {delta.preimage!r} — "
            "cannot restore."
        )

    target = _apply_target_for(delta_id, brain_root)
    if target is None:
        raise ValueError(f"no recorded apply target for delta {delta_id!r}.")
    # restore the target file verbatim from the pre-image (the revert)
    shutil.copy2(delta.preimage, target)
    _append_event(
        {"kind": "revert", "id": delta_id, "restored": target,
         "from_preimage": delta.preimage, "at": _now()},
        brain_root=brain_root,
    )
    render_markdown(brain_root)
    return get_delta(delta_id, brain_root)  # type: ignore[return-value]


def _apply_target_for(delta_id: str, brain_root) -> str | None:
    """The most recent apply event's target path for this delta (where revert
    restores to)."""
    target = None
    for ev in _read_events(brain_root):
        if ev.get("id") == delta_id and ev.get("kind") == "apply":
            target = ev.get("target")
    return target


# --------------------------------------------------------------------------- #
# SUPERSEDE — folded into a later/bigger delta (name it)                        #
# --------------------------------------------------------------------------- #


def supersede(delta_id: str, by: str, *, brain_root=None) -> SkillDelta:
    """Mark a delta SUPERSEDED by a later/bigger one (named). A real outcome that
    stops it re-surfacing without claiming it was applied or rejected."""
    delta = get_delta(delta_id, brain_root)
    if delta is None:
        raise ValueError(f"no such delta: {delta_id!r}")
    if delta.status != PROPOSED:
        raise ValueError(
            f"refusing to supersede delta {delta_id!r}: status is "
            f"{delta.status!r}, expected {PROPOSED!r}."
        )
    if not by or not str(by).strip():
        raise ValueError("supersede requires the id of the superseding delta.")
    _append_event(
        {"kind": "supersede", "id": delta_id, "by": str(by).strip(), "at": _now()},
        brain_root=brain_root,
    )
    render_markdown(brain_root)
    return get_delta(delta_id, brain_root)  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Markdown render — the operator-facing (Type-2) view of the ledger            #
# --------------------------------------------------------------------------- #


def render_markdown(brain_root=None) -> str:
    """Render the current ledger state as an operator-facing Markdown view and
    write it next to the JSONL. Deterministic; regenerated from the event stream
    (never hand-edited — the JSONL is the source of truth)."""
    deltas = list_deltas(brain_root)
    opens = open_deltas(brain_root)
    resolved = [d for d in deltas if not d.is_open()]

    lines = [
        "# Skill-Deltas Ledger — the close-the-loop tracker",
        "",
        "> Operator-facing view (Type-2). Regenerated from "
        "`skill-deltas-ledger.jsonl` — do not hand-edit; edit via the "
        "`skill_deltas` ops (capture / apply / reject / revert).",
        "",
        "## In plain terms",
        "",
        "Every proposed skill-change gets ONE row here and is tracked until it is "
        "either APPLIED (folded into the skill, with a one-command revert) or "
        "REJECTED (with a reason). Nothing is allowed to silently rot. Nothing "
        "auto-applies — the operator does one pass at the morning gate.",
        "",
        f"**Open (awaiting the operator): {len(opens)}** · "
        f"applied: {sum(1 for d in resolved if d.status == APPLIED)} · "
        f"rejected: {sum(1 for d in resolved if d.status == REJECTED)} · "
        f"superseded: {sum(1 for d in resolved if d.status == SUPERSEDED)}",
        "",
        "## OPEN (proposed — the morning gate resolves these)",
        "",
    ]
    if not opens:
        lines.append("_(none open)_")
    for d in opens:
        lines.extend(_render_delta_block(d))
    lines += ["", "## RESOLVED (applied / rejected / superseded)", ""]
    if not resolved:
        lines.append("_(none resolved yet)_")
    for d in sorted(resolved, key=lambda x: (x.born, x.id)):
        lines.extend(_render_delta_block(d))

    body = "\n".join(lines) + "\n"
    path = ledger_md_path(brain_root)
    _assert_under_loop_root(path, brain_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return body


def _render_delta_block(d: SkillDelta) -> list[str]:
    rec = f" · recurrence {d.recurrence}x" if d.recurrence > 1 else ""
    block = [
        f"### {d.id} · {d.skill} · {d.what}",
        f"- status: {d.status} · priority: {d.priority}{rec}",
        f"- owner: {d.owner}",
        f"- root cause: {d.root_cause}",
        f"- why (cost of NOT doing it): {d.why}",
        f"- anchors: {', '.join(f'`{a}`' for a in d.anchors) or '(none)'}",
    ]
    if d.born:
        block.append(f"- born: {d.born}")
    if d.resolution:
        block.append(f"- resolution: {d.resolution}")
    if d.preimage:
        block.append(f"- pre-image (revert point): `{d.preimage}`")
    block.append("")
    return block


# --------------------------------------------------------------------------- #
# JOURNAL-AWARE wrapper — the learning-loop v2 delta (PROPOSALS-ONLY)          #
# --------------------------------------------------------------------------- #
#
# WHY a wrapper, not new SkillDelta fields (plain terms): SkillDelta is the row
# the operator's morning gate already understands, folded from a frozen ledger
# event schema. The v2 journal adds five descriptive fields (lesson, symptom,
# review, concept-touched, graduation) that belong to the JOURNAL, not to the
# ledger row's identity. Bolting them onto the ledger event would touch the
# apply/reject/revert machinery for zero gain. Instead this thin wrapper pairs
# the journal fields with the SAME underlying SkillDelta produced by the existing
# ``capture()`` — reused verbatim, recurrence/escalation and proposed-only
# guarantees intact. The wrapper NEVER writes to any skill file (the graduation
# flag is a surfaced proposal; the operator applies it later via ``apply()``).

# Adversarial CONCUR/REFUTE — the twin-peer verdict recorded on a delta.
CONCUR = "CONCUR"
REFUTE = "REFUTE"
# graduation states (the recurrence gate; proposals only — never auto-applied).
GRAD_NONE = "none"
GRAD_READY = "ready"   # recurrence>=2 + CONCUR -> SURFACED for operator apply


@dataclass(frozen=True)
class JournalDelta:
    """A SkillDelta enriched with its journal context — the v2 record the fold
    surfaces. ``delta`` is the underlying ledger row (from ``capture()``); the rest
    is the journal chain. ``graduation == 'ready'`` is a PROPOSAL the operator
    applies — it is NEVER auto-written into a skill (assert in ``capture_journal``).
    """

    delta: SkillDelta
    lesson: str = ""
    symptom: str = ""                 # the recurrence key (the underlying miss)
    review: str = ""                  # "CONCUR by <r>" | "REFUTE by <r>; …" | ""
    concept_touched: tuple[str, ...] = ()
    graduation: str = GRAD_NONE       # none | ready (a surfaced proposal)

    # convenience pass-throughs (so callers needn't reach through .delta)
    @property
    def id(self) -> str:
        return self.delta.id

    @property
    def status(self) -> str:
        return self.delta.status

    @property
    def recurrence(self) -> int:
        return self.delta.recurrence

    def is_ready_to_graduate(self) -> bool:
        return self.graduation == GRAD_READY


def review_concurs(review: str) -> bool:
    """True iff a recorded review's verdict is CONCUR (the only verdict that can
    graduate a recurrence). REFUTE / empty / anything else never graduates."""
    head = re.sub(r"\s+", " ", str(review).strip().lower()).split(" ", 1)[0]
    return head == CONCUR.lower()


def capture_journal(
    *,
    skill: str,
    root_cause: str,
    what: str,
    why: str,
    owner: str,
    anchor: str,
    lesson: str = "",
    symptom: str = "",
    review: str = "",
    concept_touched: Iterable[str] = (),
    priority: str = "med",
    brain_root=None,
) -> JournalDelta:
    """Capture a journal-context skill-delta: REUSE ``capture()`` for the ledger
    row (so recurrence/escalation + proposed-only behavior is identical), then wrap
    it with the journal fields and compute the graduation flag.

    GRADUATION (proposals-only): a delta whose underlying row has now recurred
    (``recurrence >= 2`` — the same skill+root_cause captured a 2nd time) AND whose
    review is a CONCUR is flagged ``graduation='ready'``. That flag is a SURFACED
    PROPOSAL: the operator applies it later via ``apply()``. This function asserts
    it has NOT touched any skill file — graduation here is metadata, never an edit.
    """
    # delegate the ledger write to the existing, proven capture (do not reimplement)
    delta = capture(
        skill=skill, root_cause=root_cause, what=what, why=why, owner=owner,
        anchor=anchor, priority=priority, brain_root=brain_root,
    )

    graduated = delta.recurrence >= 2 and review_concurs(review)
    jd = JournalDelta(
        delta=delta,
        lesson=lesson,
        symptom=symptom,
        review=review,
        concept_touched=tuple(concept_touched),
        graduation=GRAD_READY if graduated else GRAD_NONE,
    )

    # HARD GUARANTEE: graduation surfaces a proposal; it MUST NOT apply anything.
    # The only way a delta leaves ``proposed`` is a separate operator ``apply()``
    # (which pairs an APPLIED row with an archived pre-image). Assert that here so
    # a future edit that tries to auto-apply on graduation trips this immediately.
    assert jd.delta.status == PROPOSED, (
        "capture_journal must NEVER move a delta off 'proposed' — graduation is a "
        "surfaced PROPOSAL the operator applies, never an auto-apply."
    )
    return jd


__all__ = [
    "SkillDelta",
    "JournalDelta", "capture_journal", "review_concurs",
    "CONCUR", "REFUTE", "GRAD_NONE", "GRAD_READY",
    "PROPOSED", "APPLIED", "REJECTED", "SUPERSEDED",
    "loop_root", "ledger_path", "ledger_md_path", "preimage_dir",
    "capture", "capture_correction", "apply", "reject", "revert", "supersede",
    "list_deltas", "open_deltas", "get_delta", "render_markdown",
]
