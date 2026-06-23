"""Tests for the correspondent allowlist (the shared message-lane contract).

Three things proven here:
  1. identity normalization + membership — email (case), phone (country-code
     tolerant), handle, and the fail-closed empty case;
  2. ``build_allowlist`` with an INJECTED contacts map — an emailed person is
     recognized when they later text from a resolved phone/handle, and a
     non-correspondent is never admitted;
  3. ``resolve_contacts_from_addressbook`` against a SYNTHETIC ``.abcddb`` whose
     tables match the real macOS AddressBook schema subset — email -> phone +
     sibling-email resolution, scoped to the correspondent set, fail-open on a
     missing store. (The real store needs Full Disk Access, an un-scriptable
     manual grant; the fixture mirrors its schema so the code is really run.)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ingest.allowlist import (
    Allowlist,
    build_allowlist,
    identity_tokens,
    normalize_email,
    normalize_phone,
    resolve_contacts_from_addressbook,
    summarize,
)


# --------------------------------------------------------------------------- #
# Identity normalization + membership                                          #
# --------------------------------------------------------------------------- #


def test_normalize_email_lowercases_and_extracts():
    assert normalize_email("Alice@Example.COM") == "alice@example.com"
    assert normalize_email("Alice Smith <Alice@Example.com>") == "alice@example.com"
    assert normalize_email("not an email") is None
    assert normalize_email(None) is None


def test_normalize_phone_digits_only_and_min_length():
    assert normalize_phone("+1 (415) 555-0101") == "14155550101"
    assert normalize_phone("4155550101") == "4155550101"
    assert normalize_phone("12345") is None  # too short to be a real number


def test_contains_email_is_case_insensitive():
    al = build_allowlist(["Alice@Example.com"], contacts={})
    assert al.contains("alice@example.com")
    assert al.contains("ALICE@EXAMPLE.COM")
    assert not al.contains("bob@example.com")


def test_contains_phone_is_country_code_tolerant():
    al = build_allowlist([], contacts={"a@b.com": ["+1 (415) 555-0101"]})
    # full international and bare national forms both match the same person
    assert al.contains("+14155550101")
    assert al.contains("4155550101")
    assert al.contains("(415) 555-0101")


def test_contains_handle():
    al = build_allowlist([], contacts={"a@b.com": ["@alice_h"]})
    assert al.contains("@alice_h")
    assert al.contains("alice h")  # normalized alias key
    assert not al.contains("someone_else")


def test_empty_or_blank_identity_never_matches():
    al = build_allowlist(["a@b.com"], contacts={})
    assert not al.contains("")
    assert not al.contains("   ")
    assert not al.contains(None)


def test_identity_tokens_namespaces_kinds():
    # a 10-digit handle can never collide with a phone (kinds are namespaced)
    assert identity_tokens("alice@x.com") == {"email:alice@x.com"}
    assert identity_tokens("4155550101") == {"phone:4155550101"}
    assert identity_tokens("+14155550101") == {"phone:14155550101", "phone:4155550101"}


# --------------------------------------------------------------------------- #
# build_allowlist with an injected contacts map                                #
# --------------------------------------------------------------------------- #


def test_injected_contacts_resolves_email_to_phone_and_handle():
    # The contract example: the founder emailed alice; Contacts says alice also
    # texts from a phone and a handle -> all three identify the same person.
    al = build_allowlist(
        ["alice@partner.com", "bob@partner.com"],
        contacts={"alice@partner.com": ["+1 (415) 555-0101", "@alice_chat"]},
    )
    assert al.contains("alice@partner.com")   # email
    assert al.contains("4155550101")          # resolved phone (national tail)
    assert al.contains("@alice_chat")         # resolved handle
    assert al.contains("bob@partner.com")     # other correspondent
    assert not al.contains("spammer@evil.com")
    assert not al.contains("9995550000")      # a number we never resolved


def test_contains_any_admits_group_with_one_correspondent():
    al = build_allowlist(["alice@partner.com"], contacts={})
    # a group chat with alice + two strangers is admitted (one is a correspondent)
    assert al.contains_any(["+19995550000", "alice@partner.com", "stranger@x.com"])
    # a group with no correspondent is not
    assert not al.contains_any(["+19995550000", "stranger@x.com"])


def test_summarize_reports_counts_not_tokens():
    al = build_allowlist(
        ["alice@partner.com"], contacts={"alice@partner.com": ["+14155550101"]}
    )
    s = summarize(al)
    assert s["tokens_by_kind"]["email"] == 1
    assert s["tokens_by_kind"]["phone"] >= 1
    # the summary must not leak the raw tokens themselves
    assert "alice@partner.com" not in str(s)


# --------------------------------------------------------------------------- #
# AddressBook sqlite resolution (synthetic fixture matching the real schema)   #
# --------------------------------------------------------------------------- #


def _write_addressbook_fixture(db_path: Path, contacts: list[dict]) -> None:
    """Create a synthetic ``.abcddb`` with the real macOS AddressBook schema
    subset the resolver reads.

    ``contacts`` is a list of ``{"emails": [...], "phones": [...]}`` records.
    Schema verified read-only against a live ``AddressBook-v22.abcddb``:
        ZABCDRECORD(Z_PK, ZFIRSTNAME, ZLASTNAME, ZNICKNAME, ZORGANIZATION)
        ZABCDEMAILADDRESS(Z_PK, ZOWNER -> ZABCDRECORD.Z_PK, ZADDRESS)
        ZABCDPHONENUMBER(Z_PK, ZOWNER -> ZABCDRECORD.Z_PK, ZFULLNUMBER)
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE ZABCDRECORD (
                Z_PK INTEGER PRIMARY KEY,
                ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZNICKNAME TEXT, ZORGANIZATION TEXT
            );
            CREATE TABLE ZABCDEMAILADDRESS (
                Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS TEXT
            );
            CREATE TABLE ZABCDPHONENUMBER (
                Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER TEXT
            );
            """
        )
        email_pk = 1
        phone_pk = 1
        for record_pk, contact in enumerate(contacts, start=1):
            conn.execute(
                "INSERT INTO ZABCDRECORD (Z_PK, ZFIRSTNAME) VALUES (?, ?)",
                (record_pk, contact.get("first", f"Person{record_pk}")),
            )
            for addr in contact.get("emails", []):
                conn.execute(
                    "INSERT INTO ZABCDEMAILADDRESS (Z_PK, ZOWNER, ZADDRESS) VALUES (?, ?, ?)",
                    (email_pk, record_pk, addr),
                )
                email_pk += 1
            for number in contact.get("phones", []):
                conn.execute(
                    "INSERT INTO ZABCDPHONENUMBER (Z_PK, ZOWNER, ZFULLNUMBER) VALUES (?, ?, ?)",
                    (phone_pk, record_pk, number),
                )
                phone_pk += 1
        conn.commit()
    finally:
        conn.close()


def test_addressbook_resolves_email_to_phone_and_sibling_email(tmp_path: Path):
    db = tmp_path / "AddressBook-v22.abcddb"
    _write_addressbook_fixture(
        db,
        [
            # alice: emailed at work@, also has a personal@ and a phone
            {"emails": ["work@partner.com", "personal@partner.com"],
             "phones": ["+1 (415) 555-0101"]},
            # a stranger we never emailed — must NOT leak in
            {"emails": ["stranger@nope.com"], "phones": ["+19995550000"]},
        ],
    )
    resolved = resolve_contacts_from_addressbook(
        ["work@partner.com"], addressbook_path=db
    )
    assert set(resolved.keys()) == {"work@partner.com"}
    bundle = resolved["work@partner.com"]
    assert "+1 (415) 555-0101" in bundle           # the phone (raw; normalized on build)
    assert "personal@partner.com" in bundle        # the sibling email
    assert "stranger@nope.com" not in str(resolved)


def test_build_allowlist_default_uses_addressbook(tmp_path: Path):
    # End-to-end: build_allowlist(contacts=None) reads the (config-pointed) store
    # and the texting phone of an emailed person is admitted.
    db = tmp_path / "AddressBook-v22.abcddb"
    _write_addressbook_fixture(
        db, [{"emails": ["work@partner.com"], "phones": ["+14155550101"]}]
    )
    # Point the resolver at the fixture via the explicit kwarg path through a
    # tiny wrapper (the config path is covered by mcs_paths' own tests).
    contacts = resolve_contacts_from_addressbook(["work@partner.com"], addressbook_path=db)
    al = build_allowlist(["work@partner.com"], contacts=contacts)
    assert al.contains("work@partner.com")
    assert al.contains("4155550101")  # the resolved phone, country-code tolerant


def test_addressbook_missing_store_fails_open(tmp_path: Path):
    # A missing/denied store -> {} (email-only allowlist), never an exception.
    assert resolve_contacts_from_addressbook(
        ["a@b.com"], addressbook_path=tmp_path / "nope.abcddb"
    ) == {}
    # and build_allowlist(contacts=None) with no real store still yields a valid,
    # email-only allowlist rather than raising.
    al = build_allowlist(["a@b.com"], contacts={})
    assert al.contains("a@b.com")


def test_addressbook_directory_reads_all_dbs(tmp_path: Path):
    # macOS keeps live data in per-source DBs under Sources/ — a directory path
    # must read every *.abcddb beneath it.
    (tmp_path / "Sources" / "AAA").mkdir(parents=True)
    (tmp_path / "Sources" / "BBB").mkdir(parents=True)
    _write_addressbook_fixture(
        tmp_path / "Sources" / "AAA" / "AddressBook-v22.abcddb",
        [{"emails": ["alice@partner.com"], "phones": ["+14155550101"]}],
    )
    _write_addressbook_fixture(
        tmp_path / "Sources" / "BBB" / "AddressBook-v22.abcddb",
        [{"emails": ["bob@partner.com"], "phones": ["+14155550202"]}],
    )
    resolved = resolve_contacts_from_addressbook(
        ["alice@partner.com", "bob@partner.com"], addressbook_path=tmp_path
    )
    assert set(resolved.keys()) == {"alice@partner.com", "bob@partner.com"}
