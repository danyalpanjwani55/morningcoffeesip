"""The data-boundary egress rail (SDL-23) — reusable, dependency-free module.

Lifted verbatim out of ``genesis/genesis_contracts.py`` so ANY skill (not just
genesis) can guard a foreign-model prompt with the same fail-closed classifier:
the steering skills, ``atomic-decompose``, ``/close``, etc. Nothing private
leaves to a foreign / multi-vendor model. ``guard()`` raises
``PrivateDataEgressError`` on private content; unclassifiable text (empty /
whitespace / None) is treated as private. Stdlib-only; no network, no file I/O.

``genesis_contracts`` re-exports ``EgressGate`` + ``PrivateDataEgressError`` from
here, so the existing ``from genesis_contracts import EgressGate`` call sites in
the pipeline keep working unchanged.
"""

from __future__ import annotations

import itertools
import re
from typing import Iterable


class PrivateDataEgressError(RuntimeError):
    """Raised when private content would leave to a foreign model."""


# Generic privacy patterns (reuse the spirit of ingest_privacy_gate). These
# catch the obvious secret / credential / PII / contract-body shapes. The rule
# is fail-CLOSED: anything that matches -> private; unclassifiable -> private.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # API-key / token shapes
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                       # AWS access key id
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),                   # GitHub PAT
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),           # Slack token
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),         # PEM private key
    re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|access[_-]?token|"
               r"client[_-]?secret|bearer)\b\s*[:=]\s*\S+"),
    # 2FA / OTP
    re.compile(r"(?i)\b(one[- ]?time (pass)?code|2fa code|otp)\b\s*[:#]?\s*\d{4,8}"),
)
_PII_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                      # US SSN
    re.compile(r"\b(?:\d[ -]?){13,16}\b"),                     # credit-card-ish run
)
_CONTRACT_BODY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bthis (agreement|contract) is (made|entered into)\b"),
    re.compile(r"(?i)\bin witness whereof\b"),
    re.compile(r"(?i)\bconfidential(?:ity)?\b.*\bshall not\b"),
)

_ALL_PRIVATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    _SECRET_PATTERNS + _PII_PATTERNS + _CONTRACT_BODY_PATTERNS
)


class EgressGate:
    """The data-boundary classifier + guard.

    ``classify(text) -> "public" | "private"`` — public only when none of the
    private patterns match. ``guard(text) -> text`` — returns the text if
    public, else raises ``PrivateDataEgressError``. Empty / whitespace-only /
    None text is treated as **private** (unclassifiable -> fail closed).

    The default ``extra_patterns`` hook lets a deployment add company-specific
    private shapes without subclassing.
    """

    def __init__(self, extra_patterns: Iterable[re.Pattern[str]] | None = None):
        self._patterns: tuple[re.Pattern[str], ...] = tuple(
            itertools.chain(_ALL_PRIVATE_PATTERNS, extra_patterns or ())
        )

    def classify(self, text: str | None) -> str:
        if text is None or not text.strip():
            return "private"          # unclassifiable -> fail closed
        for pat in self._patterns:
            if pat.search(text):
                return "private"
        return "public"

    def guard(self, text: str) -> str:
        """Return ``text`` iff it's safe to send to a foreign model; else raise.

        Use as: ``llm.complete(system, egress.guard(user))`` so a private
        prompt can never reach the model.
        """
        if self.classify(text) == "private":
            raise PrivateDataEgressError(
                "Refusing egress: prompt contains private/unclassifiable content "
                "(secrets, credentials, PII, or contract body)."
            )
        return text
