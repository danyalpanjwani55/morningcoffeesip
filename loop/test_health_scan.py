"""Tests for loop/recall_health — the oversize-page canary.

Deterministic; no network; wiki tree planted in tmp_path.

Pins the contract (spec item 2.3 — recall-health oversize detector):
  * a planted OVERSIZE page (line count > cap) is flagged;
  * a NORMAL page (at/under the cap) is NOT flagged;
  * the cap comes from the SHARED genesis bounded-write constant
    ``genesis_pipeline._MAX_DRAFT_LINES`` — not a hardcoded literal;
  * SURFACE ONLY — the scan never mutates a page (file bytes unchanged);
  * an absent wiki tree is healthy (no findings, no crash);
  * findings name the page + its line count + the cap, worst offender first.

Run: ``/usr/bin/python3 -B -m pytest -q loop/test_health_scan.py``.
"""

from __future__ import annotations

import os

import recall_health as RH
from genesis_pipeline import _MAX_DRAFT_LINES


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _page(path: str, n_lines: int) -> None:
    """Write a markdown page of exactly ``n_lines`` lines under ``path``."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"line {i}" for i in range(n_lines)))


def _wiki(tmp_path) -> str:
    return str(tmp_path / "brain" / "wiki")


# --------------------------------------------------------------------------- #
# The done-bar: oversize flagged, normal not, cap is the shared constant       #
# --------------------------------------------------------------------------- #


def test_planted_oversize_page_is_flagged(tmp_path):
    root = _wiki(tmp_path)
    big = os.path.join(root, "ava", "index.md")
    _page(big, _MAX_DRAFT_LINES + 50)  # well over the cap

    findings = RH.scan_oversize_pages(root)

    assert [f.path for f in findings] == [big]
    (only,) = findings
    assert only.lines == _MAX_DRAFT_LINES + 50
    assert only.cap == _MAX_DRAFT_LINES


def test_normal_page_is_not_flagged(tmp_path):
    root = _wiki(tmp_path)
    _page(os.path.join(root, "ava", "log.md"), _MAX_DRAFT_LINES - 10)

    assert RH.scan_oversize_pages(root) == []


def test_exactly_at_cap_is_not_flagged(tmp_path):
    """The cap is a ceiling, not a trip wire: a page AT the cap is in-bounds
    (strictly-greater-than is the genesis bounded-write semantics)."""
    root = _wiki(tmp_path)
    _page(os.path.join(root, "potter", "page.md"), _MAX_DRAFT_LINES)

    assert RH.scan_oversize_pages(root) == []


def test_cap_comes_from_shared_constant_not_a_literal(tmp_path):
    """The threshold MUST be the genesis bounded-write cap. Pin it by behavior:
    a page sized to the live constant ±1 lands on the right side of the line,
    so the detector is wired to ``_MAX_DRAFT_LINES`` and not to a stray 200."""
    root = _wiki(tmp_path)
    _page(os.path.join(root, "a", "under.md"), _MAX_DRAFT_LINES)       # in-bounds
    over = os.path.join(root, "b", "over.md")
    _page(over, _MAX_DRAFT_LINES + 1)                                  # one past

    findings = RH.scan_oversize_pages(root)

    assert [f.path for f in findings] == [over]
    assert findings[0].cap == _MAX_DRAFT_LINES
    # The function's own default cap is the shared constant, not a literal.
    # (cap is keyword-only -> __kwdefaults__, not __defaults__.)
    assert RH.scan_oversize_pages.__kwdefaults__["cap"] == _MAX_DRAFT_LINES


def test_mixed_tree_flags_only_oversize_worst_first(tmp_path):
    root = _wiki(tmp_path)
    _page(os.path.join(root, "ava", "log.md"), 10)                    # fine
    mild = os.path.join(root, "ava", "mild.md")
    _page(mild, _MAX_DRAFT_LINES + 5)                                 # over by 5
    worst = os.path.join(root, "potter", "worst.md")
    _page(worst, _MAX_DRAFT_LINES + 99)                              # over by 99
    _page(os.path.join(root, "potter", "notes.txt".replace(".txt", ".md")), 3)

    findings = RH.scan_oversize_pages(root)

    # Only the two oversize pages, worst offender first.
    assert [f.path for f in findings] == [worst, mild]
    assert findings[0].over_by == 99
    assert findings[1].over_by == 5


def test_non_markdown_files_are_ignored(tmp_path):
    root = _wiki(tmp_path)
    huge_txt = os.path.join(root, "ava", "dump.txt")
    os.makedirs(os.path.dirname(huge_txt), exist_ok=True)
    with open(huge_txt, "w", encoding="utf-8") as fh:
        fh.write("\n".join(str(i) for i in range(_MAX_DRAFT_LINES + 500)))

    assert RH.scan_oversize_pages(root) == []


def test_absent_wiki_tree_is_healthy(tmp_path):
    """A brain with no wiki yet is healthy, not broken — no crash, no findings."""
    assert RH.scan_oversize_pages(str(tmp_path / "nope" / "wiki")) == []


def test_surface_only_never_mutates_a_page(tmp_path):
    """SURFACE ONLY: scanning an oversize page must not edit/split/trim it."""
    root = _wiki(tmp_path)
    big = os.path.join(root, "ava", "index.md")
    _page(big, _MAX_DRAFT_LINES + 30)
    before = open(big, encoding="utf-8").read()

    RH.scan_oversize_pages(root)

    assert open(big, encoding="utf-8").read() == before


def test_custom_cap_is_honored(tmp_path):
    """Cap is overridable (for callers/tests); a small explicit cap trips a
    page the shared cap would pass."""
    root = _wiki(tmp_path)
    page = os.path.join(root, "ava", "small.md")
    _page(page, 25)

    assert RH.scan_oversize_pages(root) == []            # under the 200 cap
    findings = RH.scan_oversize_pages(root, cap=10)      # trips a cap of 10
    assert [f.path for f in findings] == [page]
    assert findings[0].cap == 10
