"""Allowlist — the people the founder actually corresponds with.

The shared contract every message lane (iMessage / WhatsApp / future) consumes.
The rule the whole system turns on: a message is only ingested if at least one
of its participants is someone the founder *already corresponds with*. That set
is derived from the email SENT folder (the addresses the founder has emailed) —
the email lane PRODUCES it; the message lanes CONSUME an injected ``Allowlist``
and DROP any message whose sender/participants are not in it.

De-welded from the company brain's ``imessage-ingest.py`` ``Allowlist`` (the
identity-token model: an email map, a phone map, an alias/handle map, and the
``extract_emails`` / ``normalize_phone`` / ``normalize_alias`` normalizers, plus
the idea of resolving a person's Contacts handles). What carried over is the
genuinely reusable spine — normalize an arbitrary identity (address, phone, or
handle) to a comparable token, then test membership — and a Contacts resolver,
re-implemented as a direct **read-only read of the AddressBook sqlite** (no UI
automation). What was deleted (company-specific, NOT reusable): the YAML policy
file, the per-person slug roster read off ``pillars/2-people``, the AppleScript
Contacts bridge, the lane/global split, group-admit policy, and the reroute-slug
machinery. None of that is needed to answer the one question a lane asks: *is
this identity someone the founder corresponds with?*

An identity is one of three shapes, normalized so the same human matches however
they appear:
  * an **email address**   -> lowercased, e.g. ``Alice@X.com`` -> ``alice@x.com``
  * a **phone number**     -> digits only, with a 10-digit tail alias so
    ``+1 (415) 555-0101`` and ``4155550101`` match (country-code tolerant)
  * a **handle**           -> a generic alias key (lowercased, punctuation
    collapsed) for anything that's neither an email nor a phone.

``build_allowlist(sent_correspondents, contacts=None)`` is the constructor the
email lane calls: ``sent_correspondents`` are the addresses the founder has
emailed; the ``contacts`` map resolves each correspondent to the phone numbers /
handles they also reach the founder by (so an email contact is recognized when
they text). Pass ``contacts`` explicitly (a clone on another OS, or a test) and
the build is pure + in-memory. Leave it ``None`` on a Mac and the map is read
from the local macOS Contacts store (the AddressBook sqlite) automatically — a
denied/absent store fails OPEN to an email-only allowlist, never an error.

Stdlib-only. The only optional I/O is a read-only open of the AddressBook sqlite
when ``contacts=None`` and a real store is present; everything else is in-memory.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import mcs_paths  # noqa: E402

# Config key (resolved via ``mcs_paths``) pointing at a macOS AddressBook store
# to resolve email -> phone/handle when no ``contacts`` map is injected. Accepts
# a directory (``~/Library/Application Support/AddressBook`` — every ``*.abcddb``
# under it is read, because the live data sits in per-source DBs) or a single
# ``.abcddb`` file. Absent -> the standard macOS location -> else no Contacts.
CONFIG_ADDRESSBOOK_KEY = "addressbook_path"

# --------------------------------------------------------------------------- #
# Identity normalizers (de-welded from the live Allowlist's helpers)           #
# --------------------------------------------------------------------------- #

_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)


def normalize_email(value: str | None) -> str | None:
    """The first email address in ``value``, lowercased; else ``None``.

    Accepts a bare address or a ``Name <addr@host>`` form (extracts the addr).
    """
    if not value:
        return None
    match = _EMAIL_RE.search(value)
    return match.group(0).lower() if match else None


def normalize_phone(value: str | None) -> str | None:
    """Digits-only phone, or ``None`` if fewer than 10 digits.

    A real phone has >=10 digits; anything shorter is not a dialable number and
    is rejected (so a short numeric handle doesn't masquerade as a phone).
    """
    if not value:
        return None
    digits = re.sub(r"\D+", "", value)
    return digits if len(digits) >= 10 else None


def phone_tokens(value: str | None) -> set[str]:
    """All comparable phone tokens for a number: the full digit string AND, when
    it carries a country code (>10 digits), its 10-digit national tail.

    This makes ``+14155550101`` and ``4155550101`` match the same person — the
    country-code-tolerant compare the live lane relied on.
    """
    phone = normalize_phone(value)
    if not phone:
        return set()
    tokens = {phone}
    if len(phone) > 10:
        tokens.add(phone[-10:])
    return tokens


def normalize_alias(value: str | None) -> str | None:
    """A generic alias key: lowercased, non-alphanumeric runs collapsed to single
    spaces, trimmed. ``None`` if nothing remains. Used for handles that are
    neither an email nor a phone (e.g. a chat username)."""
    if not value:
        return None
    lowered = re.sub(r"[^a-z0-9]+", " ", value.lower())
    normalized = re.sub(r"\s+", " ", lowered).strip()
    return normalized or None


def identity_tokens(identity: str | None) -> set[str]:
    """Every comparable token for one raw identity.

    Dispatch by shape: an email yields one ``email:`` token; a phone yields one
    or two ``phone:`` tokens (full + national tail); anything else yields one
    ``alias:`` token. The tokens are namespaced by kind so a 10-digit alias can
    never collide with a phone. Returns an empty set for empty/blank input
    (which therefore never matches anything — fail-closed).
    """
    if not identity or not str(identity).strip():
        return set()
    raw = str(identity).strip()

    email = normalize_email(raw)
    if email:
        return {f"email:{email}"}

    phones = phone_tokens(raw)
    if phones:
        return {f"phone:{p}" for p in phones}

    alias = normalize_alias(raw)
    return {f"alias:{alias}"} if alias else set()


# --------------------------------------------------------------------------- #
# The Allowlist                                                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Allowlist:
    """The set of identity tokens the founder corresponds with.

    ``tokens`` is the flat set of namespaced tokens (``email:...`` / ``phone:...``
    / ``alias:...``) any participant is tested against. Build it with
    ``build_allowlist`` rather than by hand. ``.contains(identity)`` is the one
    question a lane asks per participant.
    """

    tokens: frozenset[str] = field(default_factory=frozenset)

    def contains(self, identity: str | None) -> bool:
        """True iff ``identity`` (an email, phone, or handle) belongs to someone
        the founder corresponds with.

        An identity matches if ANY of its normalized tokens is in the set (so a
        ``+1``-prefixed number matches its bare national form, and case/format
        differences in an email don't matter). Empty/blank/unknown identities
        never match — the lane drops them.
        """
        toks = identity_tokens(identity)
        if not toks:
            return False
        return any(tok in self.tokens for tok in toks)

    def contains_any(self, identities: Iterable[str | None]) -> bool:
        """True iff at least one of ``identities`` is allowlisted. The admission
        test for a (possibly group) chat: keep it if any participant is someone
        the founder corresponds with."""
        return any(self.contains(i) for i in identities)

    def __len__(self) -> int:
        return len(self.tokens)

    def __bool__(self) -> bool:
        return bool(self.tokens)


def build_allowlist(
    sent_correspondents: Iterable[str],
    contacts: Mapping[str, Iterable[str]] | None = None,
) -> Allowlist:
    """Build the allowlist from the founder's sent-correspondents.

    Args:
        sent_correspondents: the identities the founder has reached out to —
            in practice the email addresses pulled from the SENT folder by the
            email lane (but any identity shape is accepted and normalized).
        contacts: map ``correspondent -> [other identities]`` resolving an
            emailed person to the phone numbers / handles they ALSO reach the
            founder by, so someone the founder emails is recognized when they
            text from a number that never appeared in email. Both the key (the
            correspondent) and each value are normalized and added.

            When ``contacts`` is ``None`` (the default) the map is resolved from
            the local macOS Contacts store via
            :func:`resolve_contacts_from_addressbook` — limited to the
            ``sent_correspondents`` (the whole address book is NOT the
            allowlist). Pass ``{}`` to force an email-only allowlist with no
            Contacts read; pass an explicit map off-Mac or in a test.

    Every input is run through ``identity_tokens`` so the same person matches
    however they later appear in a message lane. Returns a frozen ``Allowlist``.
    """
    tokens: set[str] = set()
    correspondent_emails: set[str] = set()
    for correspondent in sent_correspondents:
        toks = identity_tokens(correspondent)
        tokens |= toks
        for tok in toks:
            if tok.startswith("email:"):
                correspondent_emails.add(tok.split(":", 1)[1])

    if contacts is None:
        contacts = resolve_contacts_from_addressbook(correspondent_emails)

    if contacts:
        for correspondent, extra_identities in contacts.items():
            tokens |= identity_tokens(correspondent)
            for extra in extra_identities or ():
                tokens |= identity_tokens(extra)

    return Allowlist(tokens=frozenset(tokens))


def summarize(allowlist: Allowlist) -> dict[str, Any]:
    """A small, non-sensitive shape for logging/diagnostics: token COUNTS by
    kind, never the tokens themselves (a token can be a real phone/email)."""
    counts = {"email": 0, "phone": 0, "alias": 0}
    for tok in allowlist.tokens:
        kind = tok.split(":", 1)[0]
        if kind in counts:
            counts[kind] += 1
    return {"token_total": len(allowlist.tokens), "tokens_by_kind": counts}


# --------------------------------------------------------------------------- #
# macOS Contacts resolution — email -> phone/handle (default when contacts=None)#
# --------------------------------------------------------------------------- #
#
# The schema subset we read (verified read-only against a live
# ``AddressBook-v22.abcddb``; stable across recent macOS):
#   ZABCDRECORD(Z_PK, ZFIRSTNAME, ZLASTNAME, ZNICKNAME, ZORGANIZATION)
#   ZABCDEMAILADDRESS(Z_PK, ZOWNER -> ZABCDRECORD.Z_PK, ZADDRESS)
#   ZABCDPHONENUMBER(Z_PK, ZOWNER -> ZABCDRECORD.Z_PK, ZFULLNUMBER)
# The "name match" is implicit + exact-by-construction: every email and phone on
# the SAME contact record is tied together by that record, which is precisely
# "these handles all belong to one named person."
_SQL_EMAILS_BY_RECORD = (
    "SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS "
    "WHERE ZADDRESS IS NOT NULL AND ZOWNER IS NOT NULL"
)
_SQL_PHONES_BY_RECORD = (
    "SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER "
    "WHERE ZFULLNUMBER IS NOT NULL AND ZOWNER IS NOT NULL"
)


def resolve_contacts_from_addressbook(
    correspondent_emails: Iterable[str],
    *,
    addressbook_path: str | os.PathLike[str] | None = None,
) -> dict[str, list[str]]:
    """Resolve correspondent emails -> their other identities from macOS Contacts.

    Returns the same shape :func:`build_allowlist` consumes — ``{email:
    [identities]}`` — where, for every contact one of whose emails is in
    ``correspondent_emails``, the value lists that contact's phone numbers AND
    their other email addresses. So a person you email at ``work@`` is also
    matched when they text, or when they email from ``personal@``.

    Store resolution (first hit wins): the ``addressbook_path`` argument > the
    ``addressbook_path`` config key (via ``mcs_paths``) > the standard
    ``~/Library/Application Support/AddressBook`` location. Reading the real
    store also requires macOS **Full Disk Access** — an un-scriptable manual
    grant — so a missing store, a denied read, or any sqlite/OS error returns
    ``{}`` (fail-OPEN: the allowlist stays email-only, still correct, just
    narrower). Opened strictly read-only; the live store is never mutated.
    """
    wanted = {e for e in (normalize_email(x) for x in correspondent_emails) if e}
    if not wanted:
        return {}

    out: dict[str, list[str]] = {}
    for db_path in _addressbook_db_paths(addressbook_path):
        try:
            _harvest_addressbook_db(db_path, wanted, out)
        except (sqlite3.Error, OSError):
            # Fail-open per store: a locked/denied/corrupt DB contributes
            # nothing rather than breaking the whole allowlist build.
            continue
    return out


def _addressbook_db_paths(explicit: str | os.PathLike[str] | None) -> list[Path]:
    """The AddressBook ``.abcddb`` files to read, resolved + existence-filtered.

    A directory -> every ``*.abcddb`` beneath it (macOS keeps the live data in
    per-source DBs under ``Sources/``); a file -> just that file; nothing -> [].
    """
    if explicit is not None:
        base = mcs_paths._norm(explicit)
    else:
        cfg = mcs_paths._read_config().get(CONFIG_ADDRESSBOOK_KEY)
        base = (
            mcs_paths._norm(cfg)
            if cfg
            else mcs_paths._norm(
                Path.home() / "Library" / "Application Support" / "AddressBook"
            )
        )
    if base.is_dir():
        return sorted(p for p in base.rglob("*.abcddb") if p.is_file())
    if base.is_file():
        return [base]
    return []


def _harvest_addressbook_db(
    db_path: Path, wanted: set[str], out: dict[str, list[str]]
) -> None:
    """Read one ``.abcddb`` (read-only) and merge resolved identities into ``out``.

    For each contact record owning a wanted email, gather all of that record's
    phones + other emails, and attach them to every wanted email on the record.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    try:
        emails_by_record: dict[Any, list[str]] = {}
        for owner, address in conn.execute(_SQL_EMAILS_BY_RECORD):
            norm = normalize_email(address)
            if norm is not None:
                emails_by_record.setdefault(owner, []).append(norm)
        phones_by_record: dict[Any, list[str]] = {}
        for owner, number in conn.execute(_SQL_PHONES_BY_RECORD):
            if normalize_phone(number) is not None:
                phones_by_record.setdefault(owner, []).append(str(number))
    finally:
        conn.close()

    for owner, record_emails in emails_by_record.items():
        if not wanted.intersection(record_emails):
            continue
        extras = list(phones_by_record.get(owner, []))
        for primary in record_emails:
            if primary not in wanted:
                continue
            bucket = out.setdefault(primary, [])
            for identity in (*extras, *record_emails):
                # The primary itself is already a correspondent token; its
                # phones + sibling emails are the new resolutions.
                if identity != primary and identity not in bucket:
                    bucket.append(identity)
