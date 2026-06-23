"""Tests for the two real source adapters (deterministic on-disk fixtures).

LocalFilesAdapter: a tmp dir of .md/.txt notes.
EmailAdapter: a tmp dir of .eml files AND a tmp mbox file (stdlib mailbox).
"""

from __future__ import annotations

import mailbox
import os
import textwrap
from pathlib import Path

from ingest.adapters import EmailAdapter, LocalFilesAdapter


# --------------------------------------------------------------------------- #
# LocalFilesAdapter                                                            #
# --------------------------------------------------------------------------- #


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text), encoding="utf-8")


def test_local_files_reads_md_and_txt(tmp_path: Path):
    _write(tmp_path / "a.md", "# Note A\nlaunch_date = 2026-10-15\n")
    _write(tmp_path / "sub" / "b.txt", "some plain note about pricing\n")
    _write(tmp_path / "skip.pdf", "binary-ish")  # wrong extension -> skipped

    records = list(LocalFilesAdapter(root=tmp_path).read())
    ids = sorted(r["source_id"] for r in records)
    assert ids == ["a.md", os.path.join("sub", "b.txt")]
    a = next(r for r in records if r["source_id"] == "a.md")
    assert a["kind"] == "note"
    assert "launch_date = 2026-10-15" in a["text"]
    assert a["title"] == "a"
    assert a["observed_at"].endswith("Z")


def test_local_files_source_id_is_relative_not_absolute(tmp_path: Path):
    # No home/absolute path may leak into the corpus (machine-independence).
    _write(tmp_path / "deep" / "c.md", "content")
    [rec] = list(LocalFilesAdapter(root=tmp_path).read())
    assert rec["source_id"] == os.path.join("deep", "c.md")
    assert str(tmp_path) not in rec["source_id"]


def test_local_files_missing_dir_yields_nothing(tmp_path: Path):
    assert list(LocalFilesAdapter(root=tmp_path / "does-not-exist").read()) == []


def test_local_files_deterministic_order(tmp_path: Path):
    for name in ("z.md", "a.md", "m.md"):
        _write(tmp_path / name, f"note {name}")
    ids = [r["source_id"] for r in LocalFilesAdapter(root=tmp_path).read()]
    assert ids == sorted(ids)


# --------------------------------------------------------------------------- #
# EmailAdapter — the SENT-folder correspondent filter                          #
#                                                                              #
# The founder (you@me.com) emailed alice@partner.com. So an inbound from alice #
# is a real correspondent (KEPT); an inbound from a newsletter/spammer the     #
# founder never wrote to is DROPPED. These fixtures prove exactly that.        #
# --------------------------------------------------------------------------- #

USER = "you@me.com"

# A SENT message (From == the founder, To a real correspondent) — also carries
# the Gmail "Sent" label so the label path is exercised too.
_SENT_EML = """\
From: You <you@me.com>
To: Alice <alice@partner.com>
Cc: Carol <carol@partner.com>
Subject: re: the deal
Message-ID: <sent-001@me.com>
Date: Fri, 19 Jun 2026 09:00:00 +0000
X-Gmail-Labels: Sent,Important
Content-Type: text/plain; charset="utf-8"

Looping you both in.
"""

# An INBOUND message from a real correspondent (alice, whom the founder emailed).
_INBOUND_CORRESPONDENT_EML = """\
From: Alice <alice@partner.com>
To: You <you@me.com>
Subject: Q4 pricing
Message-ID: <msg-001@partner.com>
Date: Sat, 20 Jun 2026 10:00:00 +0000
Content-Type: text/plain; charset="utf-8"

list_price = 5200
Let's lock this for the first deal.
"""

# An INBOUND from a sender the founder NEVER emailed — spam/newsletter. DROP it.
_INBOUND_SPAM_EML = """\
From: Deals <noreply@spam-newsletter.example>
To: You <you@me.com>
Subject: 80% OFF EVERYTHING!!!
Message-ID: <spam-001@spam-newsletter.example>
Date: Sat, 20 Jun 2026 11:00:00 +0000
Content-Type: text/plain; charset="utf-8"

Click here to claim your prize.
"""


def _build_mbox(path: Path, *eml_texts: str) -> None:
    box = mailbox.mbox(str(path))
    box.lock()
    try:
        for text in eml_texts:
            box.add(text)
        box.flush()
    finally:
        box.unlock()
        box.close()


def test_email_mbox_keeps_correspondent_drops_spam(tmp_path: Path):
    # The headline contract: a Sent folder + one real correspondent + one
    # spammer -> inbound spam dropped, correspondent kept.
    mbox_path = tmp_path / "all.mbox"
    _build_mbox(mbox_path, _SENT_EML, _INBOUND_CORRESPONDENT_EML, _INBOUND_SPAM_EML)

    adapter = EmailAdapter(path=mbox_path, user_email=USER)
    records = adapter.build()
    ids = sorted(r["source_id"] for r in records)

    # Only the correspondent's inbound survives. The sent message itself is the
    # seed (not re-ingested); the spam is dropped.
    assert ids == ["msg-001@partner.com"]
    # The correspondent set was harvested from the sent message's To + Cc, minus
    # the founder's own address.
    assert adapter.sent_correspondents == {"alice@partner.com", "carol@partner.com"}
    kept = records[0]
    assert kept["kind"] == "email"
    assert kept["subject"] == "Q4 pricing"
    assert "list_price = 5200" in kept["text"]
    assert kept["observed_at"] == "Sat, 20 Jun 2026 10:00:00 +0000"


def test_email_mbox_sent_detected_by_gmail_label_without_user_email(tmp_path: Path):
    # Even with NO configured user address, the Gmail "Sent" label alone marks
    # the sent message, so the correspondent set is still built.
    mbox_path = tmp_path / "all.mbox"
    _build_mbox(mbox_path, _SENT_EML, _INBOUND_CORRESPONDENT_EML, _INBOUND_SPAM_EML)

    adapter = EmailAdapter(path=mbox_path)  # no user_email
    records = adapter.build()
    assert sorted(r["source_id"] for r in records) == ["msg-001@partner.com"]
    assert "alice@partner.com" in adapter.sent_correspondents


def test_email_eml_dir_sent_is_from_user(tmp_path: Path):
    # In a flat .eml dir, a sent item is identified by From == the user address.
    (tmp_path / "1-sent.eml").write_text(_SENT_EML, encoding="utf-8")
    (tmp_path / "2-correspondent.eml").write_text(_INBOUND_CORRESPONDENT_EML, encoding="utf-8")
    (tmp_path / "3-spam.eml").write_text(_INBOUND_SPAM_EML, encoding="utf-8")

    adapter = EmailAdapter(path=tmp_path, user_email=USER)
    records = adapter.build()
    assert sorted(r["source_id"] for r in records) == ["msg-001@partner.com"]
    assert adapter.sent_correspondents == {"alice@partner.com", "carol@partner.com"}


def test_email_eml_dir_without_user_email_ingests_nothing(tmp_path: Path):
    # Fail-closed: a flat .eml dir with no way to tell sent from inbound must
    # ingest NOTHING (better empty than an unfiltered spam-laden corpus).
    (tmp_path / "1-sent.eml").write_text(_SENT_EML, encoding="utf-8")
    (tmp_path / "2-correspondent.eml").write_text(_INBOUND_CORRESPONDENT_EML, encoding="utf-8")

    adapter = EmailAdapter(path=tmp_path)  # no user_email, no Gmail labels usable in .eml mode
    assert adapter.build() == []
    assert adapter.skip_reason == "eml_dir_requires_user_email"


def test_email_multipart_keeps_plain_text_only(tmp_path: Path):
    # Body extraction (plain-text only, HTML dropped) for a KEPT correspondent.
    sent = (
        "From: you@me.com\r\n"
        "To: a@x.com\r\n"
        "Subject: hi\r\n"
        "Message-ID: <s@me>\r\n"
        "X-Gmail-Labels: Sent\r\n"
        "Content-Type: text/plain\r\n\r\n"
        "seed\r\n"
    )
    multipart = (
        'From: a@x.com\r\n'
        'To: you@me.com\r\n'
        'Subject: mixed\r\n'
        'Message-ID: <mp@x>\r\n'
        'MIME-Version: 1.0\r\n'
        'Content-Type: multipart/alternative; boundary="B"\r\n'
        '\r\n'
        '--B\r\n'
        'Content-Type: text/plain; charset="utf-8"\r\n\r\n'
        'the clean plain body\r\n'
        '--B\r\n'
        'Content-Type: text/html; charset="utf-8"\r\n\r\n'
        '<p>HTML we must NOT keep</p>\r\n'
        '--B--\r\n'
    )
    (tmp_path / "1-sent.eml").write_text(sent, encoding="utf-8")
    (tmp_path / "2-mp.eml").write_text(multipart, encoding="utf-8")
    [rec] = EmailAdapter(path=tmp_path, user_email=USER).build()
    assert rec["text"] == "the clean plain body"
    assert "HTML" not in rec["text"]


def test_email_missing_message_id_uses_content_digest(tmp_path: Path):
    # A kept correspondent message with no Message-ID gets a stable content id.
    sent = (
        "From: you@me.com\r\n"
        "To: a@x.com\r\n"
        "Subject: hi\r\n"
        "Message-ID: <s@me>\r\n"
        "X-Gmail-Labels: Sent\r\n"
        "Content-Type: text/plain\r\n\r\n"
        "seed\r\n"
    )
    no_id = (
        "From: a@x.com\r\n"
        "To: you@me.com\r\n"
        "Subject: no id here\r\n"
        "Content-Type: text/plain\r\n\r\n"
        "body without a message id\r\n"
    )
    (tmp_path / "1-sent.eml").write_text(sent, encoding="utf-8")
    (tmp_path / "2-noid.eml").write_text(no_id, encoding="utf-8")
    [rec] = EmailAdapter(path=tmp_path, user_email=USER).build()
    assert rec["source_id"].startswith("sha256-")


def test_email_maildir_sent_folder(tmp_path: Path):
    # A Maildir store with a real "Sent" folder: messages in Sent define the
    # correspondents; inbound from a correspondent is kept, spam dropped.
    box = mailbox.Maildir(str(tmp_path / "md"), create=True)
    sent = box.add_folder("Sent")
    sent.add(_SENT_EML)  # From you@me.com -> alice + carol
    box.add(_INBOUND_CORRESPONDENT_EML)  # from alice -> kept
    box.add(_INBOUND_SPAM_EML)           # from a stranger -> dropped
    box.close()

    adapter = EmailAdapter(path=tmp_path / "md", user_email=USER)
    records = adapter.build()
    assert sorted(r["source_id"] for r in records) == ["msg-001@partner.com"]
    assert adapter.sent_correspondents == {"alice@partner.com", "carol@partner.com"}


def test_email_sent_cc_to_self_does_not_whitelist_self(tmp_path: Path):
    # A sent mail that Cc's the founder's own address must NOT make the founder
    # their own correspondent (which would let their whole inbox through).
    self_cc_sent = _SENT_EML.replace(
        "Cc: Carol <carol@partner.com>", "Cc: Me <you@me.com>"
    )
    (tmp_path / "1-sent.eml").write_text(self_cc_sent, encoding="utf-8")
    adapter = EmailAdapter(path=tmp_path, user_email=USER)
    adapter.build()
    assert adapter.sent_correspondents == {"alice@partner.com"}


def test_email_missing_path_yields_nothing(tmp_path: Path):
    assert list(EmailAdapter(path=tmp_path / "nope").read()) == []
