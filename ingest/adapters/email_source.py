"""EmailAdapter — ingest email, filtered to the founder's real correspondents.

The other near-universal founder source, and the lane that DEFINES who the
founder corresponds with. Uses only stdlib ``mailbox`` + ``email``: no IMAP, no
network, no credentials — the founder exports/points at a local mbox or a folder
of ``.eml`` files (every mail client can produce these).

The de-spam contract (de-welded from the live brain's ``email-classifier.py``,
which keyed sent-vs-inbound off Gmail labels and filtered to known
correspondents): the lane is NOT "ingest every email." It:

  1. reads the **SENT** items and builds the **correspondent set** — every
     address the founder has *emailed* (each sent message's To + Cc);
  2. ingests an inbound message **only if its sender is in that set** — so
     newsletters, cold outreach, and spam (senders the founder never wrote to)
     are DROPPED, and the founder's real two-way relationships flow through;
  3. exposes the correspondent set (``sent_correspondents``) so the caller can
     hand it to ``ingest.allowlist.build_allowlist`` — the SAME people then gate
     the iMessage / WhatsApp lanes. The email lane PRODUCES the allowlist seed;
     the message lanes CONSUME it.

How "SENT" is identified, by source shape:
  * **mbox** — a single mbox carries sent + inbound together; a sent message is
    marked by an ``X-Gmail-Labels`` header containing ``Sent`` (the Gmail/Takeout
    export reality the live classifier relied on) OR by ``From`` == the
    configured user address. (Plain ``mailbox.mbox`` has no folders; the label
    header is how a flat mbox encodes the Sent folder.)
  * **Maildir / MH** — real folders: the ``Sent`` (or ``Sent Mail`` /
    ``Sent Items``) folder is the sent set, every other folder is inbound.
  * **.eml directory** — a flat pile of messages; a sent item is one whose
    ``From`` == the configured user address, the rest are inbound.

The user's own address resolves (first hit wins): the ``user_email=`` argument >
``$MCS_USER_EMAIL`` > the ``user_email`` config key (via ``mcs_paths``). Without
it, mbox falls back to the ``X-Gmail-Labels: Sent`` signal alone, and a flat
``.eml`` dir cannot tell sent from inbound — so it INGESTS NOTHING and records
the reason (fail-closed: better no corpus than an unfiltered one full of spam).

Per kept (inbound) message we extract the same fields as before: ``source_id``
(canonical ``Message-ID``, else a content digest), ``observed_at`` (the ``Date``
header), ``participants`` (From + To + Cc, lowercased), ``text`` (the
``text/plain`` body; HTML is skipped, not de-tagged), and ``subject``/``title``.

Stdlib-only. The only I/O is reading the mbox/eml you point it at. No writes.
"""

from __future__ import annotations

import email
import mailbox
import os
import sys
from email.message import Message
from email.utils import getaddresses
from pathlib import Path
from typing import Any, Iterable, Iterator

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import mcs_paths  # noqa: E402

from ingest.dedupe import stable_digest  # noqa: E402

# Config key (via ``mcs_paths``) + env var for the founder's own address — used
# to identify sent messages (From == user) when no Gmail-label signal is present.
CONFIG_USER_EMAIL_KEY = "user_email"
ENV_USER_EMAIL = "MCS_USER_EMAIL"

# Folder names (lowercased) treated as "sent" in a Maildir/MH store.
_SENT_FOLDER_NAMES = {"sent", "sent mail", "sent items", "[gmail]/sent mail"}

# The Gmail/Takeout label header + the label value that marks a sent message.
_GMAIL_LABELS_HEADER = "X-Gmail-Labels"
_SENT_LABEL = "sent"


class EmailAdapter:
    """Yield one raw record per INBOUND email from a real correspondent.

    Args:
        path: an mbox FILE, a Maildir/MH DIRECTORY, or a DIRECTORY of ``.eml``
            files. Default: ``<brain_root>/sources/mail`` via ``mcs_paths``. A
            missing path yields nothing.
        user_email: the founder's own address (used to detect sent messages by
            ``From``). Default: ``$MCS_USER_EMAIL`` > config ``user_email``.
        kind: the genesis ``kind`` stamped on every record.

    After :meth:`read` (or :meth:`build`) the attribute ``sent_correspondents``
    holds the set of addresses the founder has emailed — the allowlist seed.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        user_email: str | None = None,
        kind: str = "email",
    ):
        self.path = (
            mcs_paths._norm(path)
            if path is not None
            else mcs_paths.brain_root() / "sources" / "mail"
        )
        self.user_email = _normalize_addr(_resolve_user_email(user_email))
        self.kind = kind
        # Populated by read()/build(): the addresses the founder has emailed.
        self.sent_correspondents: set[str] = set()
        # Reason the lane ingested nothing meaningful (diagnostic; never raw).
        self.skip_reason: str = ""

    def read(self) -> Iterator[dict[str, Any]]:
        """Yield one record per inbound message from a known correspondent.

        Two passes over the source: first collect every sent message's To/Cc
        into ``sent_correspondents``, then yield each inbound message whose
        sender is in that set. Sent messages themselves are not re-ingested as
        inbound (the founder's own outbound is captured as correspondents, not
        as records). Order is deterministic.
        """
        messages = list(self._iter_source_messages())
        self.sent_correspondents = self._collect_correspondents(messages)

        if not self.sent_correspondents and not self.skip_reason:
            # No correspondents discoverable (no Sent signal at all) -> nothing
            # can pass the filter. Record why, then yield nothing.
            self.skip_reason = "no_sent_messages_identified"

        for is_sent, msg in messages:
            if is_sent:
                continue  # the founder's own sends are the seed, not inbound records
            sender = _sender_addr(msg)
            if not sender or sender not in self.sent_correspondents:
                continue  # spam / newsletter / cold inbound -> DROP
            record = self._record_from_message(msg)
            if record is not None:
                yield record

    def build(self) -> list[dict[str, Any]]:
        """Eager :meth:`read` -> a list (so ``sent_correspondents`` is populated
        for the caller without manually draining the generator)."""
        return list(self.read())

    # -- source iteration --------------------------------------------------- #

    def _iter_source_messages(self) -> Iterator[tuple[bool, Message]]:
        """Yield ``(is_sent, message)`` for every message in the source.

        Dispatches on the source shape: a Maildir/MH store (has folders), a flat
        ``.eml`` directory, or a single mbox file. A missing path yields nothing.
        """
        if self.path.is_dir():
            if _looks_like_maildir(self.path) or _looks_like_mh(self.path):
                yield from self._iter_folder_store()
            else:
                yield from self._iter_eml_dir()
        elif self.path.is_file():
            yield from self._iter_mbox()
        # missing path -> nothing

    def _iter_mbox(self) -> Iterator[tuple[bool, Message]]:
        box = mailbox.mbox(str(self.path))
        try:
            for key in sorted(box.keys()):
                msg = box.get_message(key)
                yield (self._mbox_message_is_sent(msg), msg)
        finally:
            box.close()

    def _iter_eml_dir(self) -> Iterator[tuple[bool, Message]]:
        if not self.user_email:
            # Can't tell sent from inbound without the user's address.
            self.skip_reason = "eml_dir_requires_user_email"
            return
        for path in sorted(self.path.rglob("*.eml"), key=lambda p: str(p)):
            if not path.is_file():
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            msg = email.message_from_bytes(raw)
            is_sent = _sender_addr(msg) == self.user_email
            yield (is_sent, msg)

    def _iter_folder_store(self) -> Iterator[tuple[bool, Message]]:
        box: mailbox.Mailbox
        if _looks_like_maildir(self.path):
            box = mailbox.Maildir(str(self.path), create=False)
        else:
            box = mailbox.MH(str(self.path), create=False)
        try:
            # Top-level folder = inbound by default; only named Sent folders are
            # sent. (A user address still lets From==user count as sent too.)
            for key in sorted(box.keys()):
                msg = box.get_message(key)
                yield (self._mbox_message_is_sent(msg), msg)
            for folder_name in sorted(box.list_folders()):
                is_sent_folder = folder_name.strip().lower() in _SENT_FOLDER_NAMES
                folder = box.get_folder(folder_name)
                try:
                    for key in sorted(folder.keys()):
                        msg = folder.get_message(key)
                        yield (is_sent_folder or self._mbox_message_is_sent(msg), msg)
                finally:
                    folder.close()
        finally:
            box.close()

    def _mbox_message_is_sent(self, msg: Message) -> bool:
        """A message is sent if a Gmail ``Sent`` label is present, or (when the
        user address is known) its ``From`` is the user."""
        labels = _header(msg, _GMAIL_LABELS_HEADER).lower()
        if labels:
            parts = {p.strip() for p in labels.split(",")}
            if _SENT_LABEL in parts:
                return True
        if self.user_email and _sender_addr(msg) == self.user_email:
            return True
        return False

    # -- correspondent harvest + record build ------------------------------- #

    def _collect_correspondents(
        self, messages: Iterable[tuple[bool, Message]]
    ) -> set[str]:
        """Every address the founder emailed = the To + Cc of each sent message.

        The founder's own address is excluded (you don't correspond with
        yourself), so a self-cc'd sent mail doesn't whitelist your own inbox.

        The harvest RULE itself lives in the module-level
        :func:`harvest_sent_correspondents` so a non-mbox lane (the cloud Gmail
        lane, which has dict records rather than ``Message`` objects) reuses the
        exact same logic instead of forking it. This method just supplies the
        per-message ``(To+Cc addresses)`` of each SENT message from this mbox.
        """
        sent_recipient_lists = (
            _addresses(msg, ("To", "Cc")) for is_sent, msg in messages if is_sent
        )
        return harvest_sent_correspondents(sent_recipient_lists, user_email=self.user_email)

    def _record_from_message(self, msg: Message) -> dict[str, Any] | None:
        body = _plain_text_body(msg)
        subject = _header(msg, "Subject")
        message_id = _canonical_message_id(_header(msg, "Message-ID"))
        source_id = message_id or f"sha256-{stable_digest({'s': subject, 'b': body})}"
        participants = _addresses(msg, ("From", "To", "Cc"))
        return {
            "kind": self.kind,
            "source_type": self.kind,
            "source_id": source_id,
            "locator": "",
            "text": body,
            "subject": subject,
            "title": subject,
            "observed_at": _header(msg, "Date"),
            "participants": participants,
            "meta": {"adapter": "email", "from": _addresses(msg, ("From",))[:1]},
        }


def harvest_sent_correspondents(
    sent_recipient_lists: Iterable[Iterable[str]], *, user_email: str = ""
) -> set[str]:
    """The canonical SENT-folder → correspondent rule, source-shape-agnostic.

    Given the recipient (To + Cc) address lists of the founder's SENT messages
    — one inner iterable per sent message — return the set of every address the
    founder emailed, with the founder's own address excluded (you don't
    correspond with yourself; a self-cc'd sent mail must not whitelist your own
    inbox). Addresses are lowercased so the set matches case-insensitively.

    This is the ONE place the rule lives. ``EmailAdapter._collect_correspondents``
    (mbox/maildir/.eml) and the cloud Gmail lane (dict records) both call it, so
    the spam-vs-correspondent boundary can never drift between the two sources.
    The resulting set is the seed handed to
    :func:`ingest.allowlist.build_allowlist` — the SAME allowlist that then gates
    the iMessage / WhatsApp lanes.
    """
    me = (user_email or "").strip().lower()
    out: set[str] = set()
    for recipients in sent_recipient_lists:
        for addr in recipients:
            norm = (addr or "").strip().lower()
            if norm and norm != me:
                out.add(norm)
    return out


def _resolve_user_email(explicit: str | None) -> str:
    """The founder's own address: explicit arg > ``$MCS_USER_EMAIL`` > config."""
    if explicit:
        return explicit
    env = os.environ.get(ENV_USER_EMAIL)
    if env:
        return env
    cfg = mcs_paths._read_config().get(CONFIG_USER_EMAIL_KEY)
    return cfg or ""


def _normalize_addr(value: str) -> str:
    """Lowercase + extract a bare address from a possibly ``Name <addr>`` form."""
    if not value:
        return ""
    parsed = getaddresses([value])
    if parsed and parsed[0][1]:
        return parsed[0][1].strip().lower()
    return value.strip().lower()


def _sender_addr(msg: Message) -> str:
    """The single From address, lowercased (``""`` if absent)."""
    addrs = _addresses(msg, ("From",))
    return addrs[0] if addrs else ""


def _header(msg: Message, name: str) -> str:
    value = msg.get(name)
    return str(value).strip() if value else ""


def _addresses(msg: Message, header_names: Iterable[str]) -> list[str]:
    raw = [msg.get(h, "") for h in header_names]
    out: list[str] = []
    seen: set[str] = set()
    for _name, addr in getaddresses(raw):
        addr = (addr or "").strip().lower()
        if addr and addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


def _looks_like_maildir(path: Path) -> bool:
    """A Maildir has the three spool subdirs ``cur`` / ``new`` / ``tmp``."""
    return all((path / sub).is_dir() for sub in ("cur", "new", "tmp"))


def _looks_like_mh(path: Path) -> bool:
    """An MH mailbox is marked by a ``.mh_sequences`` file."""
    return (path / ".mh_sequences").is_file()


def _plain_text_body(msg: Message) -> str:
    """Return the message's plain-text body.

    Prefers a ``text/plain`` part; ignores attachments and HTML parts (we keep
    only clean text rather than risk leaking markup or binary into the corpus).
    """
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_content_type() != "text/plain":
                continue
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            text = _decode_part(part)
            if text:
                return text.strip()
        return ""
    if msg.get_content_type() == "text/plain":
        return (_decode_part(msg) or "").strip()
    return ""


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        # not a bytes payload (e.g. nested) -> best-effort string
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, ValueError):
        return payload.decode("utf-8", errors="replace")


def _canonical_message_id(value: str) -> str:
    text = value.strip().lower()
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()
    return text
