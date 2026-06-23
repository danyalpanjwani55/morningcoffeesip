#!/usr/bin/env python3
"""run.py — the on-ramp, end to end, as ONE runnable journey.

This is the front door a founder (or a stranger, on the bundled sample) walks to
turn a pile of raw sources into the start of a company brain. It wires the four
pieces that already exist into a single guided flow:

    point at sources  →  INGEST (ingest/ spine: sanitize → normalize → dedup)
        →  run_genesis (genesis/: claims → resolve → pillars → meta-initiatives
            → propose roster → review packet)
        →  print the Type-2 review packet (FOR-THE-OPERATOR, "In plain terms")
        →  RATIFY (the operator approves/edits/rejects each proposal — the gate)
        →  for each RATIFIED agent: build its cited DRAFT wiki (agent_wiki_builder)
        →  hand off to the steer loop (skills/morning + loop/) for the cadence.

This matches the documented genesis flow (docs/SYSTEM.md):
    CONNECT → BULK INGEST → CLAIMS → RESOLVE → WRITE PILLARS → DERIVE meta-
    initiatives → PROPOSE roster → BUILD cited wikis → REVIEW SURFACE → you
    ratify → INCREMENTAL CADENCE thereafter.

Run it on the bundled sample with no setup, no accounts, no data of your own:

    python3 run.py                     # interactive: walks you through ratify
    python3 run.py --auto-ratify       # non-interactive: ratify every proposal
    python3 run.py --auto-ratify=none  # non-interactive: ratify nothing
    python3 run.py --sources path/to/your/notes_and_mail  # your own corpus

THE RAILS (CODE, not aspiration — the same ones the engine enforces):
  * **Proposals-only.** ``run_genesis`` emits ``status="proposed"`` only; NOTHING
    is applied here. A cited agent wiki is built ONLY for a proposal the operator
    explicitly RATIFIES (``build_wiki_for_ratified_proposal``, which refuses a
    non-ratified proposal). Auto-ratify is an explicit operator choice, never a
    silent default.
  * **Privacy gate.** The ingest spine drops any record carrying a secret /
    credential / PII before it becomes an event — you'll see the count in the run
    summary; that content never reaches the model or a wiki.
  * **Data-boundary.** Every model prompt is ``egress.guard``-ed inside genesis.
  * **Confined writes.** The only writes are the genesis pillar drafts + agent
    wikis under ``genesis/out/`` (asserted by the engine's own path guards).
  * **No git, no sends, no money, no secrets.** This script never runs git, never
    sends anything, never moves money, never prints raw private content.

Built-in OFFLINE model: genesis routes ALL judgment through an injected ``llm``.
So a stranger can run this with NO API key, ``run.py`` ships a small deterministic
model (``OfflineGenesisLLM``) that reads the REAL evidence in each prompt and
proposes from it — it does NOT bypass the engine's floors (the
``MIN_EVIDENCE`` ≥3-distinct-signals roster gate and the anchor-or-drop rule
still decide what survives). Point ``run.py`` at a real model by passing an
object with ``complete(system, user, *, max_tokens)`` to ``main(..., llm=...)``.
Stdlib-only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Iterable

# This file lives at the repo root. Put the repo root + genesis/ on sys.path so
# the flat imports (the convention the engine uses) resolve from anywhere.
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_GENESIS = os.path.join(_REPO_ROOT, "genesis")
for _p in (_REPO_ROOT, _GENESIS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ingest import EmailAdapter, IngestedCorpus, LocalFilesAdapter, ingest_records  # noqa: E402
from ingest.allowlist import build_allowlist  # noqa: E402
from ingest.allowlist import summarize as summarize_allowlist  # noqa: E402
from ingest.cloud import (  # noqa: E402
    NormalizedRecords,
    correspondents_from_gmail,
    process_cloud_records,
)
from ingest.local.sync import iter_local_records  # noqa: E402

from genesis_contracts import EgressGate, Proposal  # noqa: E402
from genesis_pipeline import OUT_DIR, run_genesis  # noqa: E402
from agent_wiki_builder import build_wiki_for_ratified_proposal  # noqa: E402

# The bundled sample corpus (a fictional company — no real people/companies).
SAMPLE_DIR = os.path.join(_REPO_ROOT, "examples", "sample-company")
# The bundled sample's founder address — the email lane uses it to tell the
# founder's SENT mail (which defines who they correspond with) from inbound, so
# the demo's spam-vs-correspondent filter works with zero configuration.
SAMPLE_USER_EMAIL = "avery@auroratea.example"

# The base roster every clone ships with (the documented template — genesis
# proposes the company-specific specialists from real evidence ON TOP of this).
# Mirrors skills/morning's "base-roster template — genesis proposes the rest".
BASE_ROSTER: list[str] = [
    "coordinator",                 # chief-of-staff / orchestration (cross-cutting)
    "specialist-legal-business",   # Specialist A
    "specialist-product",          # Specialist B
    "specialist-software-build",   # Specialist C
    "operator",                    # the founder
]


# --------------------------------------------------------------------------- #
# The built-in OFFLINE model (deterministic; reads the real evidence)         #
# --------------------------------------------------------------------------- #


class OfflineGenesisLLM:
    """A tiny deterministic stand-in for a real model, so the on-ramp runs with
    NO API key on the bundled sample.

    It honors the engine's two LLM contracts by reading the REAL evidence the
    engine puts in each prompt and proposing grounded, anchored items:

      * **Meta-initiative prompt** (starts with ``PILLAR:``) — proposes ONE
        meta-initiative for the pillar, citing the first available evidence ids
        (so it is always anchored; the engine drops it otherwise).
      * **Roster prompt** (contains ``CORPUS EVIDENCE``) — scans each numbered
        evidence snippet for a small set of domain keyword clusters and proposes
        a specialist agent per cluster, citing the ids that matched. It does NOT
        enforce the ≥3 floor itself — the engine's ``roster_proposer`` re-counts
        DISTINCT anchored signals and drops anything under ``MIN_EVIDENCE`` — so
        a thin cluster correctly yields no agent.

    This is the modeling layer ONLY; every safety floor stays in the engine. Swap
    it for a real model and the same floors apply unchanged.
    """

    # Candidate specialist domains the offline model can cluster toward, each a
    # (slug, domain-label, keyword-set). Deliberately generic — these are the
    # recurring back-office functions a small company surfaces. The engine's
    # MIN_EVIDENCE floor decides which (if any) actually clear the bar, and
    # base-roster slugs are filtered by the engine, never here.
    _CLUSTERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("customer-support", "customer support",
         ("customer support", "support question", "support is", "support keeps",
          "support load", "support role", "pause a subscription", "refund",
          "help doc", " support")),
        ("sourcing-supply", "sourcing & supply",
         ("supplier", "sourcing", "vendor", "fulfillment", "3pl", "logistics",
          "inventory")),
        ("growth-marketing", "growth & marketing",
         ("launch campaign", "co-marketing", "partnership", "funnel",
          "marketing", "campaign")),
    )

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        if user.startswith("PILLAR:"):
            return self._meta_initiative(user)
        if "CORPUS EVIDENCE" in user:
            return self._roster(user)
        return "[]"

    # -- meta-initiative ---------------------------------------------------- #

    def _meta_initiative(self, user: str) -> str:
        pillar = _first_value(user, "PILLAR:")
        ids = _evidence_ids(user)
        if not ids:
            return "[]"          # nothing to anchor -> propose nothing
        cite = ids[:2]           # cite the first 1-2 available anchors
        title = f"Win the {pillar} priority for the next 24 months"
        return json.dumps([
            {
                "title": title,
                "rationale": (
                    f"Recurring {pillar} signals in the corpus point to a "
                    f"focused 24-month thrust here."
                ),
                "confidence": "medium",
                "anchor_ids": cite,
            }
        ])

    # -- roster ------------------------------------------------------------- #

    def _roster(self, user: str) -> str:
        evidence = _evidence_snippets(user)     # {id: snippet_lower}
        out: list[dict] = []
        for slug, domain, keywords in self._CLUSTERS:
            hits = sorted(
                i for i, snip in evidence.items()
                if any(kw in snip for kw in keywords)
            )
            if not hits:
                continue
            # Propose it; the engine re-counts DISTINCT signals and drops it if
            # under MIN_EVIDENCE. We surface our best signal set.
            out.append({
                "slug": slug,
                "domain": domain,
                "rationale": (
                    f"{len(hits)} distinct sources reference {domain}; recurring "
                    f"enough to warrant a dedicated specialist."
                ),
                "confidence": "medium",
                "anchor_ids": hits,
            })
        return json.dumps(out)


def _first_value(text: str, label: str) -> str:
    for line in text.splitlines():
        if line.startswith(label):
            return line[len(label):].strip() or "company"
    return "company"


def _evidence_ids(text: str) -> list[int]:
    """All ``[i]`` evidence indices present in a prompt, in order."""
    return [int(m) for m in re.findall(r"^\[(\d+)\]", text, re.MULTILINE)]


def _evidence_snippets(text: str) -> dict[int, str]:
    """Map each ``[i] ... — snippet`` evidence line to its lowercased snippet
    (the roster prompt carries the snippet after an em dash)."""
    out: dict[int, str] = {}
    for line in text.splitlines():
        m = re.match(r"^\[(\d+)\]\s*(.*)$", line)
        if not m:
            continue
        idx = int(m.group(1))
        rest = m.group(2)
        # snippet is after the em dash the roster prompt inserts; fall back to
        # the whole remainder so a format change can't silently blank it.
        snippet = rest.split("—", 1)[1] if "—" in rest else rest
        out[idx] = snippet.lower()
    return out


# --------------------------------------------------------------------------- #
# Pretty-printing helpers (plain, dependency-free)                            #
# --------------------------------------------------------------------------- #


def _rule(title: str = "") -> str:
    bar = "=" * 70
    return f"\n{bar}\n{title}\n{bar}" if title else f"\n{bar}"


def _agent_proposals(packet) -> list[Proposal]:
    return [p for p in packet.proposals if p.type == "agent"]


# --------------------------------------------------------------------------- #
# Ratify                                                                       #
# --------------------------------------------------------------------------- #


def ratify_proposals(
    packet,
    *,
    auto: str | None,
    input_fn=input,
    out_stream=None,
) -> list[Proposal]:
    """Walk the AGENT proposals and let the operator ratify each (the gate).

    Returns the list of proposals the operator RATIFIED (status flipped to
    ``"ratified"`` on a copy — the packet's own proposals stay ``"proposed"``).
    Only ratified agent proposals get a wiki built downstream.

    ``auto`` controls non-interactive runs:
      * ``None``   — interactive (prompt per proposal: ratify / reject).
      * ``"all"``  — ratify every agent proposal (operator chose --auto-ratify).
      * ``"none"`` — ratify none.

    Meta-initiatives are shown for context but are not ratified into anything
    here (no MI artifact is built in this slice); the agent proposals are the
    ones that, when ratified, stand an agent up.
    """
    out_stream = out_stream if out_stream is not None else sys.stdout

    def w(s: str = "") -> None:
        print(s, file=out_stream)

    agents = _agent_proposals(packet)
    if not agents:
        w("\nNo agent roster proposals to ratify "
          "(nothing cleared the evidence floor). Nothing to stand up.")
        return []

    w(_rule("RATIFY — the operator gate (nothing is applied until you say so)"))
    w(
        "Each item below is a PROPOSED specialist agent the engine derived from "
        "your\nrecords. Ratifying one builds its cited DRAFT wiki. Rejecting one "
        "drops it.\nNothing here is applied without your explicit yes.\n"
    )

    ratified: list[Proposal] = []
    for i, p in enumerate(agents, start=1):
        slug = p.payload.get("slug", p.id)
        domain = p.payload.get("domain", "")
        n_anchors = len(p.source_anchors)
        w(f"  ({i}) agent: {slug}"
          + (f"  ·  domain: {domain}" if domain else "")
          + f"  ·  {n_anchors} supporting reference"
          + ("s" if n_anchors != 1 else ""))
        w(f"      why: {p.rationale or '(no rationale)'}")

        if auto == "all":
            decision = "y"
            w("      → ratify (auto)")
        elif auto == "none":
            decision = "n"
            w("      → reject (auto)")
        else:
            decision = _ask(input_fn, "      ratify this agent? [y]es / [n]o: ")

        if decision == "y":
            ratified.append(_as_ratified(p))
        w()

    return ratified


def _ask(input_fn, prompt: str) -> str:
    """Read a y/n answer; default 'n' on empty/EOF (fail-closed: don't stand up
    an agent the operator didn't clearly approve)."""
    try:
        raw = input_fn(prompt)
    except EOFError:
        return "n"
    raw = (raw or "").strip().lower()
    if raw in ("y", "yes"):
        return "y"
    return "n"


def _as_ratified(p: Proposal) -> Proposal:
    """A copy of the proposal with ``status="ratified"`` — the operator's gate
    output. The original packet proposal is untouched (stays 'proposed')."""
    return Proposal(
        id=p.id,
        type=p.type,
        confidence=p.confidence,
        rationale=p.rationale,
        source_anchors=p.source_anchors,
        payload=dict(p.payload),
        status="ratified",
    )


# --------------------------------------------------------------------------- #
# Build wikis for the ratified agents                                          #
# --------------------------------------------------------------------------- #


def build_ratified_wikis(
    ratified: list[Proposal],
    corpus,
    *,
    llm,
    egress: EgressGate,
    today: str,
) -> list:
    """Build a cited DRAFT wiki for each RATIFIED agent proposal.

    Delegates to ``agent_wiki_builder.build_wiki_for_ratified_proposal``, which
    refuses any non-ratified proposal — so this can only ever build wikis for
    things the operator approved. Returns the list of ``WikiBuildResult``.
    """
    results = []
    for p in ratified:
        result = build_wiki_for_ratified_proposal(
            p, corpus, llm, egress, today=today
        )
        results.append(result)
    return results


# --------------------------------------------------------------------------- #
# The journey                                                                  #
# --------------------------------------------------------------------------- #


def run_journey(
    *,
    sources_dir: str,
    auto_ratify: str | None,
    llm=None,
    egress: EgressGate | None = None,
    input_fn=input,
    today: str | None = None,
    user_email: str | None = None,
    local_stores: dict[str, str] | None = None,
    cloud_records: NormalizedRecords | None = None,
    out_stream=None,
) -> dict:
    """Walk the whole on-ramp once and return a structured summary.

    Steps (the documented genesis flow):
      1. CONNECT  — point the adapters at the source folder.
      2. INGEST   — sanitize → normalize → dedup into a genesis corpus. The email
                    lane builds the correspondent allowlist from its SENT folder;
                    the on-device message lanes (iMessage/WhatsApp) are folded in
                    WHEN their stores are present, gated by that allowlist, and
                    cleanly skipped otherwise (off-Mac / no Full Disk Access). A
                    CLOUD connector dump (Gmail/Drive/Calendar the scheduled
                    routine already pulled) is folded in WHEN passed, through the
                    same shared spine, under the SAME ONE allowlist.
      3. GENESIS  — claims → resolve → pillars → meta-initiatives → roster →
                    review packet (proposals only).
      4. REVIEW   — print the Type-2 "In plain terms" packet.
      5. RATIFY   — operator approves/rejects each agent proposal (the gate).
      6. BUILD    — a cited DRAFT wiki per ratified agent.
      7. HANDOFF  — point the operator at the steer loop (skills/morning + loop).

    ``local_stores`` optionally maps a local lane to its store path
    (``{"imessage": "/path/chat.db", "whatsapp": "/path/ChatStorage.sqlite"}``);
    the default (``None``) uses the real macOS locations, which simply don't exist
    off-Mac, so the local lanes are a no-op there. Tests pass synthetic fixtures.

    ``cloud_records`` optionally folds in the CLOUD lane (the email/Drive/Calendar
    half of the architecture split — ``docs/INGEST-ARCHITECTURE.md``): the
    normalized connector dump the scheduled Claude routine already pulled
    (``ingest.cloud.NormalizedRecords``). Like ``local_stores`` it is OPT-IN — the
    default (``None``) means the on-ramp runs on notes + the exported mbox alone,
    and this code never reaches a connector itself (the routine does the
    auth+pull; the on-ramp only PROCESSES its output). When BOTH the exported mbox
    AND a cloud Gmail dump carry SENT mail, the founder's correspondents are the
    UNION of who they emailed in both, and ONE canonical ``Allowlist`` is built
    from that union — it gates the local message lanes AND the cloud inbound Gmail
    (the cloud lane FEEDS the one allowlist; it never owns a second one).

    Returns a dict with the ingest counts (``ingest`` + ``cloud`` when a cloud dump
    ran), the packet, the ratified proposals, and the wiki build results — the
    same things ``test_e2e.py`` / ``ingest/cloud/test_e2e_cloud.py`` assert on.
    """
    llm = llm or OfflineGenesisLLM()
    egress = egress or EgressGate()
    today = today or time.strftime("%Y-%m-%d", time.gmtime())
    # Resolve the stream at CALL time (not import time) so a redirected stdout —
    # e.g. pytest's capsys, or a caller passing its own buffer — is honored.
    if out_stream is None:
        out_stream = sys.stdout

    def out(s: str = "") -> None:
        print(s, file=out_stream)

    total = 7

    def step(n: int, label: str) -> None:
        out(f"\n[{n}/{total}] {label}")

    # --- 1. CONNECT -------------------------------------------------------- #
    notes_dir = os.path.join(sources_dir, "notes")
    mail_dir = os.path.join(sources_dir, "mail")
    # If the canonical notes/ + mail/ subfolders aren't present, fall back to
    # pointing BOTH adapters at the folder itself (a stranger may pass a flat
    # directory). Adapters read only the file types they understand.
    if not (os.path.isdir(notes_dir) or os.path.isdir(mail_dir)):
        notes_dir = mail_dir = sources_dir

    # The email lane ingests inbound ONLY from people the founder has emailed
    # (the SENT-folder filter). It needs the founder's own address to tell sent
    # from inbound in a flat .eml dir. Resolution: the explicit arg > the bundled
    # sample's founder (so the demo runs with no config) > $MCS_USER_EMAIL /
    # config (the adapter's own default). See ingest/adapters/email_source.py.
    if user_email is None and os.path.abspath(sources_dir) == os.path.abspath(SAMPLE_DIR):
        user_email = SAMPLE_USER_EMAIL

    notes_adapter = LocalFilesAdapter(root=notes_dir)
    email_adapter = EmailAdapter(path=mail_dir, user_email=user_email)
    out(_rule("MorningCoffeeSip — the on-ramp, end to end"))
    out(f"sources : {sources_dir}")
    out(f"output  : {OUT_DIR}  (pillar drafts + agent wikis land here)")
    out(f"date    : {today}")
    step(1, "CONNECT — pointing adapters at your sources")
    out(f"          notes (.md/.txt): {notes_dir}")
    out(f"          mail  (.eml)    : {mail_dir}")

    # --- 2. INGEST --------------------------------------------------------- #
    step(2, "INGEST — sanitize → normalize → dedup")
    # The email lane is BOTH a source AND a producer of the correspondent
    # allowlist: draining it once (``build()``) harvests its SENT folder into
    # ``sent_correspondents`` AND returns the inbound records.
    email_records = email_adapter.build()

    # THE ONE ALLOWLIST. The founder's correspondents are everyone they have
    # emailed — and email reaches the on-ramp from up to two sources: the exported
    # mbox (above) and, when passed, the CLOUD Gmail dump. We UNION both SENT
    # correspondent sets and build a SINGLE canonical ``Allowlist`` from the union
    # (the cloud lane's correspondents are harvested by the SAME canonical
    # ``correspondents_from_gmail`` → ``harvest_sent_correspondents`` the rest of
    # the system uses — no fork, no second allowlist). That one allowlist gates
    # BOTH the on-device message lanes AND the cloud inbound Gmail below.
    cloud_correspondents = (
        correspondents_from_gmail(cloud_records.gmail, user_email=user_email or "")
        if cloud_records is not None
        else set()
    )
    correspondents = set(email_adapter.sent_correspondents) | cloud_correspondents
    allowlist = build_allowlist(sorted(correspondents), contacts={})

    # The on-device message lanes (iMessage / WhatsApp) are folded into the SAME
    # corpus ONLY when the caller explicitly points at their stores via
    # ``local_stores`` ({"imessage": "/path/chat.db", ...}). This is opt-in by
    # design: the local agent is normally its OWN entry point
    # (``python -m ingest.local.sync``), and run.py must never silently reach into
    # a real ~/Library Messages/WhatsApp store. When given store paths, each lane
    # is still gracefully skipped if its file is absent/unreadable, gated by the
    # allowlist, and secret-stripped by the same spine that screens email + notes.
    local_records = (
        list(iter_local_records(allowlist, paths=local_stores))
        if local_stores
        else []
    )

    records = [*notes_adapter.read(), *email_records, *local_records]
    ingested = ingest_records(records, gate=egress)

    # The CLOUD lane (email/Drive/Calendar — the cloud half of the split) is folded
    # in ONLY when a connector dump is passed (opt-in, like ``local_stores``). It
    # runs through the SAME shared spine via ``process_cloud_records``, gated by
    # the ONE allowlist we just built (injected, so cloud inbound is filtered by
    # the SAME correspondent set as the local lanes — not a second one it would
    # self-build). Its surviving Events merge into the one corpus genesis consumes.
    cloud_processed = (
        process_cloud_records(
            cloud_records,
            user_email=user_email or "",
            gate=egress,
            allowlist=allowlist,
            ingested_at=today,
        )
        if cloud_records is not None
        else None
    )

    corpus = ingested.corpus
    if cloud_processed is not None:
        corpus = IngestedCorpus(
            [*ingested.corpus.all_events(), *cloud_processed.corpus.all_events()]
        )

    total_kept = ingested.kept + (cloud_processed.kept if cloud_processed else 0)
    out(f"          correspondents : {summarize_allowlist(allowlist)['token_total']} "
        f"identity token(s) from your sent mail → the one allowlist")
    out(f"          local messages : {len(local_records)} on-device record(s) "
        f"(iMessage/WhatsApp; 0 if no store / not macOS)")
    if cloud_processed is not None:
        out(f"          cloud (email/Drive/Cal): {cloud_processed.gmail_admitted} inbound "
            f"admitted of {cloud_processed.gmail_inbound} · {cloud_processed.drive_seen} doc(s) · "
            f"{cloud_processed.calendar_seen} event(s) → {cloud_processed.kept} kept")
    out(f"          kept           : {total_kept} event(s) → the corpus")
    out(f"          dropped private: {ingested.dropped_private + (cloud_processed.dropped_private if cloud_processed else 0)} "
        f"(secrets/PII never become events, never reach the model)")
    out(f"          dropped dup    : {ingested.dropped_duplicate + (cloud_processed.dropped_duplicate if cloud_processed else 0)}")
    out(f"          dropped empty  : {ingested.dropped_empty + (cloud_processed.dropped_empty if cloud_processed else 0)}")
    if total_kept == 0:
        out("\n          No usable events. Point --sources at a folder of "
            ".md/.txt notes and/or .eml mail (or pass a cloud connector dump).")
        return {
            "ingest": ingested,
            "cloud": cloud_processed,
            "packet": None,
            "ratified": [],
            "wikis": [],
        }

    # --- 3. GENESIS -------------------------------------------------------- #
    step(3, "GENESIS — claims → resolve → pillars → meta-initiatives → roster")
    packet = run_genesis(
        corpus,
        roster=BASE_ROSTER,
        since="inception",
        llm=llm,
        egress=egress,
        write_drafts=True,
        today=today,
    )
    out(f"          pillars        : {len(packet.pillars)} "
        f"({', '.join(sorted(packet.pillars)) or 'none'})")
    out(f"          proposals      : {len(packet.proposals)} "
        f"(all status=proposed — nothing applied)")
    out(f"          pillar drafts  : written under {OUT_DIR}")

    # --- 4. REVIEW --------------------------------------------------------- #
    step(4, "REVIEW — the Type-2 'In plain terms' packet (for you)")
    out(_rule())
    out(packet.summary_md.rstrip())
    out(_rule())

    # --- 5. RATIFY --------------------------------------------------------- #
    step(5, "RATIFY — your gate (nothing applied until yes)")
    ratified = ratify_proposals(
        packet, auto=auto_ratify, input_fn=input_fn, out_stream=out_stream
    )
    out(f"\n          ratified       : {len(ratified)} agent(s) "
        + (", ".join(p.payload.get("slug", p.id) for p in ratified) or "(none)"))

    # --- 6. BUILD ---------------------------------------------------------- #
    step(6, "BUILD — a cited DRAFT wiki per ratified agent")
    wikis = build_ratified_wikis(
        ratified, corpus, llm=llm, egress=egress, today=today
    )
    if wikis:
        for r in wikis:
            out(f"          built  : {r.agent_slug}  →  {r.wiki_dir}")
            out(f"                   {r.page_count} page(s): index + log + "
                f"{len(r.source_pages)} source + {len(r.concept_pages)} concept "
                f"(all 🟡 DRAFT)")
    else:
        out("          (no agent ratified → no wiki built; nothing stood up)")

    # --- 7. HANDOFF -------------------------------------------------------- #
    step(7, "HANDOFF — steer from here (the incremental cadence)")
    out(_handoff_text(out_dir=OUT_DIR, wikis=wikis))

    return {
        "ingest": ingested,
        "cloud": cloud_processed,
        "packet": packet,
        "ratified": ratified,
        "wikis": wikis,
    }


def _handoff_text(*, out_dir: str, wikis: list) -> str:
    skills = os.path.join(_REPO_ROOT, "skills")
    loop = os.path.join(_REPO_ROOT, "loop")
    built = ", ".join(r.agent_slug for r in wikis) or "(none yet)"
    return (
        "          Your brain is seeded. From here you STEER it with a small set "
        "of skills\n"
        "          and a self-improvement loop — that's the 'incremental cadence' "
        "after genesis.\n\n"
        f"          Review what was written : {out_dir}\n"
        f"          Agent wikis built       : {built}\n\n"
        "          Daily / steering loop (the three commands you actually use):\n"
        f"            • the morning gate   — {os.path.join(skills, 'morning')}\n"
        "                (review proposals, rule decisions, ratify into the board)\n"
        f"            • ramble / vision / manifest — {skills}\n"
        "                (speak your mind / clarify top-down / build bottom-up)\n"
        f"            • pulse / close      — {os.path.join(skills, 'pulse')}\n"
        "                (close a session so the brain remembers + folds it)\n\n"
        "          Keep getting smarter (the self-improvement loop):\n"
        f"            • fold     — {os.path.join(loop, 'fold.py')}\n"
        "                (compound each session's learnings into the agent wikis)\n"
        f"            • ratchet / skill_deltas — {loop}\n"
        "                (raise the bar; turn corrections into skill upgrades)\n\n"
        "          Nothing above sends, applies, or commits anything on its own — "
        "you stay the gate."
    )


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _parse_auto_ratify(value: str | None) -> str | None:
    """``--auto-ratify`` with an optional value: bare flag or ``=all`` → 'all';
    ``=none`` → 'none'; absent → None (interactive)."""
    if value is None:
        return None
    v = value.strip().lower()
    if v in ("", "all", "yes", "y", "true"):
        return "all"
    if v in ("none", "no", "n", "false"):
        return "none"
    raise SystemExit(f"--auto-ratify expects 'all' or 'none' (got {value!r})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description=(
            "Walk the MorningCoffeeSip on-ramp end to end: ingest your sources, "
            "run genesis, review the proposals, ratify, build cited agent wikis, "
            "and hand off to the steer loop. Runs on the bundled sample by "
            "default — no setup, no accounts, no data of your own."
        ),
    )
    parser.add_argument(
        "--sources",
        default=SAMPLE_DIR,
        help="folder to ingest (default: the bundled sample-company corpus). "
             "Pass a folder with a notes/ and/or mail/ subdir, or a flat folder "
             "of .md/.txt/.eml files.",
    )
    parser.add_argument(
        "--auto-ratify",
        nargs="?",
        const="all",
        default=None,
        help="run non-interactively. Bare or '=all' ratifies every proposed "
             "agent; '=none' ratifies nothing. Omit for an interactive walk.",
    )
    args = parser.parse_args(argv)

    auto = _parse_auto_ratify(args.auto_ratify)
    sources = os.path.abspath(os.path.expanduser(args.sources))
    if not os.path.isdir(sources):
        print(f"ERROR: --sources is not a directory: {sources}", file=sys.stderr)
        return 2

    run_journey(sources_dir=sources, auto_ratify=auto)
    print("\nDone. Nothing was applied, sent, or committed — every artifact is "
          "a DRAFT/proposal under genesis/out/ for you to review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
