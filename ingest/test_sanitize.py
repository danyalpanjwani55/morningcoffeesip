"""Tests for ingest.sanitize — the privacy gate that REUSES mcs_egress.

Proves: secrets/PII/credentials are dropped (not raised), clean text is kept,
the no-text case fails closed, and the gate object is genuinely mcs_egress.
"""

from __future__ import annotations

import mcs_egress
from ingest.sanitize import sanitize_record


def test_clean_record_is_allowed():
    d = sanitize_record({"text": "a normal note about the product roadmap"})
    assert d.allowed is True
    assert d.posture == "no_raw_private_content"
    assert "text" in d.screened_fields


def test_secret_record_is_dropped_not_raised():
    # mcs_egress.guard would RAISE; the ingest gate must DROP with a reason.
    d = sanitize_record({"text": "api_key = sk-ABCD1234ABCD1234ABCD"})  # pragma: allowlist secret
    assert d.allowed is False
    assert d.posture == "blocked_private"
    assert d.reason == "private_content_in_text"


def test_ssn_in_body_is_dropped():
    d = sanitize_record({"body": "his ssn is 123-45-6789 fyi"})  # pragma: allowlist secret
    assert d.allowed is False
    assert d.reason == "private_content_in_body"


def test_private_in_any_text_field_blocks():
    # A clean text but a private subject still blocks (every field screened).
    d = sanitize_record(
        {"text": "clean body", "subject": "password: hunter2longvalue"}  # pragma: allowlist secret
    )
    assert d.allowed is False
    assert d.reason == "private_content_in_subject"


def test_no_text_fails_closed():
    d = sanitize_record({"source_id": "x", "kind": "note"})
    assert d.allowed is False
    assert d.reason == "no_screenable_text"


def test_uses_mcs_egress_classifier():
    # An extra-pattern gate threads through, proving we use the real EgressGate.
    import re

    gate = mcs_egress.EgressGate(extra_patterns=[re.compile("PROJECT-NOVA")])
    blocked = sanitize_record({"text": "notes on PROJECT-NOVA"}, gate=gate)
    assert blocked.allowed is False
    allowed = sanitize_record({"text": "notes on PROJECT-NOVA"})  # default gate
    assert allowed.allowed is True
