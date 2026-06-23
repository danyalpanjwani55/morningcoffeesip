"""Tests for ingest.dedupe — the de-welded stable-key + raw-text-stripping core.

Run: rm -rf ~/Library/Caches/com.apple.python 2>/dev/null;
     /usr/bin/python3 -B -m pytest -q
"""

from __future__ import annotations

from ingest.dedupe import (
    RAW_TEXT_FIELD_NAMES,
    content_hash_for_text,
    dedupe_key,
    stable_digest,
)


def test_stable_digest_is_deterministic():
    a = stable_digest({"x": 1, "y": [2, 3]})
    b = stable_digest({"y": [2, 3], "x": 1})  # key order must not matter
    assert a == b
    assert len(a) == 32


def test_stable_digest_drops_raw_text_fields():
    # Two records differing ONLY in a raw-text field must digest identically:
    # the body text never enters the key (privacy-preserving idempotency).
    base = {"id": "m1", "ts": "2026-06-20T00:00:00Z"}
    with_text = {**base, "text": "a long private message body about a deal"}
    without = {**base, "text": "an ENTIRELY different body"}
    assert stable_digest(with_text) == stable_digest(without)
    # and a non-raw field DOES change the digest
    assert stable_digest({**base, "subject_hash": "abc"}) != stable_digest(base)


def test_every_raw_text_field_name_is_stripped():
    base = {"id": "m1"}
    for field_name in RAW_TEXT_FIELD_NAMES:
        polluted = {**base, field_name: "SECRET BODY"}
        assert stable_digest(polluted) == stable_digest(base), field_name


def test_dedupe_key_prefers_source_id():
    k1 = dedupe_key("email", source_id="<abc@x>", text="hello")
    k2 = dedupe_key("email", source_id="<abc@x>", text="totally different text")
    # same id -> same key regardless of body
    assert k1 == k2
    assert k1.startswith("email:id:sha256-")


def test_dedupe_key_falls_back_to_content():
    k1 = dedupe_key("note", source_id=None, text="same body")
    k2 = dedupe_key("note", source_id=None, text="same body")
    k3 = dedupe_key("note", source_id=None, text="different body")
    assert k1 == k2          # identical content -> identical key
    assert k1 != k3          # different content -> different key
    assert k1.startswith("note:content:sha256-")


def test_dedupe_key_is_source_agnostic():
    # An unknown/arbitrary source label just works (no enumerated allowlist).
    k = dedupe_key("some-brand-new-source", source_id="id-1", text="x")
    assert k.startswith("some-brand-new-source:id:sha256-")


def test_content_hash_shape():
    h = content_hash_for_text("hello")
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64
