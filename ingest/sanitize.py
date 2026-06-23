"""Sanitize — the privacy gate, the FIRST station of the spine.

The live company brain's ``ingest_privacy_gate.py`` returned a *decision*
(allow / block + reason codes) for each candidate rather than crashing the run —
that's the semantic that matters and is reused here. But its classifier was
welded to the company (a hardcoded Slack-channel allowlist, named real people, a
2FA policy string, FDA/IRB/patent "action-boundary" routes). The de-welded,
reusable classifier already exists at the repo root as ``mcs_egress.EgressGate``
(itself lifted out of that gate's regex spirit), so — per the build order — we
REUSE it here instead of re-implementing patterns.

The one impedance match: ``EgressGate`` is an *egress* guard whose ``guard()``
RAISES on private content (right for "don't send this to a foreign model"). An
*ingest* gate must instead DROP a private record and keep going, so the rest of
the corpus still ingests. We therefore use the non-raising half —
``EgressGate.classify()`` — and turn "private" into a skip with a reason, which
is exactly the live gate's allow/block decision shape.

Result: a secret / credential / PII-bearing record never becomes an Event; it's
dropped with a recorded reason, and the founder's clean notes flow through.
Stdlib-only (mcs_egress is stdlib); no network, no file I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from mcs_egress import EgressGate

# Mirror the live gate's text-bearing field names: the fields whose VALUES are
# human prose we must screen before keeping. (Generic; no company specifics.)
_TEXT_FIELDS = ("text", "body", "message", "content", "snippet", "title", "subject")


@dataclass(frozen=True)
class SanitizeDecision:
    """The allow/block decision for one candidate record (no raise).

    ``allowed`` False means the record is dropped from the corpus. ``reason`` is
    a short machine code (``""`` when allowed). ``posture`` mirrors the live
    gate's vocabulary at a generic level: ``"no_raw_private_content"`` (kept) or
    ``"blocked_private"`` (dropped — secrets/credentials/PII/unclassifiable).
    """

    allowed: bool
    posture: str
    reason: str = ""
    screened_fields: tuple[str, ...] = field(default_factory=tuple)


def sanitize_record(
    record: Mapping[str, Any], *, gate: EgressGate | None = None
) -> SanitizeDecision:
    """Classify one candidate record; allow it iff none of its text is private.

    Every text-bearing field is run through ``EgressGate.classify``. If ANY is
    private (a secret/credential/PII shape, or empty/unclassifiable) the record
    is blocked — fail-closed, matching the live gate. A record with no text at
    all is treated as unclassifiable -> blocked (nothing to anchor on, and we
    refuse to guess it's safe).
    """
    gate = gate or EgressGate()
    screened: list[str] = []
    found_text = False
    for name in _TEXT_FIELDS:
        value = record.get(name)
        if not isinstance(value, str) or not value.strip():
            continue
        found_text = True
        screened.append(name)
        if gate.classify(value) == "private":
            return SanitizeDecision(
                allowed=False,
                posture="blocked_private",
                reason=f"private_content_in_{name}",
                screened_fields=tuple(screened),
            )
    if not found_text:
        # Unclassifiable (no screenable text) -> fail closed, like the egress
        # gate treats empty input as private.
        return SanitizeDecision(
            allowed=False,
            posture="blocked_private",
            reason="no_screenable_text",
            screened_fields=(),
        )
    return SanitizeDecision(
        allowed=True,
        posture="no_raw_private_content",
        reason="",
        screened_fields=tuple(screened),
    )
