"""Tests for WS1 — pre-response discipline (plan items 1.1, 1.2, 1.3).

Covers the route-first + canonical-check hook mechanism:
  (a) the repo's own ``.claude/settings.json`` carries a ``UserPromptSubmit``
      hook, AND ``install.py``'s new step would install it into the clone user's
      ``~/.claude/settings.json`` (the copy/merge function is exercised directly,
      isolated under tmp — never the real ``~/.claude``).
  (b) ``agreed-framings.md`` ships at repo root and is a content-empty template
      (headings + fill-me instructions only — no company content).
  (c) the injected hook text names BOTH checks: ROUTE-FIRST and CANONICAL-CHECK.

Conventions mirror the sibling suites (test_install.py / test_skills.py): a
clean env fixture, direct function calls, tmp isolation, one concern per test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import install
import mcs_paths

REPO_ROOT = Path(__file__).resolve().parent
REPO_SETTINGS = REPO_ROOT / ".claude" / "settings.json"
AGREED_FRAMINGS = REPO_ROOT / "agreed-framings.md"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """Isolate every run: no MCS env leaking, config points into tmp so a real
    ~/.config never bleeds in. (The hook step's target dir is passed explicitly
    per-test, so the real ~/.claude is never touched.)"""
    for key in (mcs_paths.ENV_BRAIN_ROOT, mcs_paths.ENV_REPO_ROOT):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("MCS_PROJECT", raising=False)
    monkeypatch.setenv(mcs_paths.ENV_CONFIG, str(tmp_path / "cfg.json"))
    mcs_paths.reset_cache()
    yield
    mcs_paths.reset_cache()


def _repo_settings() -> dict:
    return json.loads(REPO_SETTINGS.read_text(encoding="utf-8"))


def _user_prompt_submit_text(settings: dict) -> str:
    """Concatenate every UserPromptSubmit hook command string in a settings
    doc — the text that gets injected before the model replies."""
    parts: list[str] = []
    for group in settings.get("hooks", {}).get("UserPromptSubmit", []):
        for hook in group.get("hooks", []):
            cmd = hook.get("command")
            if isinstance(cmd, str):
                parts.append(cmd)
    return "\n".join(parts)


# --- (a) the hook is present in the repo's settings ------------------------ #

def test_repo_settings_file_exists():
    assert REPO_SETTINGS.is_file(), f"missing {REPO_SETTINGS}"


def test_repo_settings_carries_user_prompt_submit_hook():
    """The repo ships a UserPromptSubmit hook with at least one command."""
    settings = _repo_settings()
    groups = settings.get("hooks", {}).get("UserPromptSubmit")
    assert isinstance(groups, list) and groups, \
        "no UserPromptSubmit hook in .claude/settings.json"
    text = _user_prompt_submit_text(settings)
    assert text.strip(), "UserPromptSubmit hook has no command text"


# --- (a) install.py's new step would install it (copy/merge function) ------ #

def test_install_hook_merges_into_user_settings(tmp_path):
    """The new install step copies the repo's UserPromptSubmit hook into the
    clone user's ~/.claude/settings.json — exercised directly, isolated."""
    user_claude = tmp_path / "dot-claude"
    wrote = install._install_hook(
        REPO_ROOT, user_claude, force=False, dry_run=False)
    assert wrote is True
    user_settings_file = user_claude / "settings.json"
    assert user_settings_file.is_file()
    user_settings = json.loads(user_settings_file.read_text(encoding="utf-8"))
    # The UserPromptSubmit hook now lives in the user's settings.
    assert user_settings.get("hooks", {}).get("UserPromptSubmit")
    # And it is the SAME hook the repo ships.
    assert (_user_prompt_submit_text(user_settings)
            == _user_prompt_submit_text(_repo_settings()))


def test_install_hook_preserves_user_keys_and_dedups(tmp_path):
    """The merge keeps the user's unrelated settings and does not duplicate the
    hook on a second run (idempotent)."""
    user_claude = tmp_path / "dot-claude"
    user_claude.mkdir()
    user_settings_file = user_claude / "settings.json"
    user_settings_file.write_text(
        json.dumps({"theme": "dark", "hooks": {"Stop": [{"hooks": []}]}}),
        encoding="utf-8")

    assert install._install_hook(
        REPO_ROOT, user_claude, force=False, dry_run=False) is True
    after = json.loads(user_settings_file.read_text(encoding="utf-8"))
    assert after["theme"] == "dark"                       # unrelated key kept
    assert "Stop" in after["hooks"]                       # unrelated hook kept
    assert after["hooks"]["UserPromptSubmit"]             # ours added

    ups_after_first = after["hooks"]["UserPromptSubmit"]
    # Second run: hook already present -> no-op (returns False, file unchanged).
    assert install._install_hook(
        REPO_ROOT, user_claude, force=False, dry_run=False) is False
    after2 = json.loads(user_settings_file.read_text(encoding="utf-8"))
    assert after2["hooks"]["UserPromptSubmit"] == ups_after_first  # no dup


def test_install_hook_dry_run_writes_nothing(tmp_path):
    user_claude = tmp_path / "dot-claude"
    wrote = install._install_hook(
        REPO_ROOT, user_claude, force=False, dry_run=True)
    assert wrote is False
    assert not (user_claude / "settings.json").exists()


def test_main_install_step_runs_against_isolated_home(monkeypatch, tmp_path):
    """End-to-end: a full install.main() run drives the hook step against an
    isolated fake home, so the real ~/.claude is never written."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    # repo_root must still point at the real repo so the shipped hook is found.
    monkeypatch.setenv(mcs_paths.ENV_REPO_ROOT, str(REPO_ROOT))
    mcs_paths.reset_cache()
    brain = tmp_path / "b"
    rc = install.main(["--brain-root", str(brain), "--project", "acme"])
    assert rc == 0
    user_settings = fake_home / ".claude" / "settings.json"
    assert user_settings.is_file(), "install.main did not run the hook step"
    settings = json.loads(user_settings.read_text(encoding="utf-8"))
    assert settings.get("hooks", {}).get("UserPromptSubmit")


# --- (b) agreed-framings.md is a content-empty template -------------------- #

def test_agreed_framings_exists():
    assert AGREED_FRAMINGS.is_file(), f"missing {AGREED_FRAMINGS}"


def test_agreed_framings_is_content_empty_template():
    """Headings + fill-me instructions only — no real company content. Every
    non-blank line must be a heading (#...), a blockquote instruction (>...), or
    an HTML-comment placeholder (<!-- ... -->). Anything else is leaked content.
    """
    lines = AGREED_FRAMINGS.read_text(encoding="utf-8").splitlines()
    leaked = [
        ln for ln in lines
        if ln.strip()
        and not ln.lstrip().startswith("#")     # headings
        and not ln.lstrip().startswith(">")      # blockquote instructions
        and not ln.lstrip().startswith("<!--")   # comment placeholders
    ]
    assert not leaked, f"agreed-framings.md carries content, not just a template: {leaked}"


def test_agreed_framings_has_the_three_headings():
    """The template exposes the slots the founder fills (mechanism, not content)."""
    body = AGREED_FRAMINGS.read_text(encoding="utf-8").lower()
    for heading in ("identity", "positioning", "numbers"):
        assert f"## {heading}" in body, f"missing heading: {heading}"


# --- (c) the injected text names BOTH checks ------------------------------- #

def test_injected_text_names_route_first_and_canonical_check():
    text = _user_prompt_submit_text(_repo_settings())
    assert "ROUTE-FIRST" in text, "hook text does not name ROUTE-FIRST"
    assert "CANONICAL-CHECK" in text, "hook text does not name CANONICAL-CHECK"


def test_injected_text_points_at_agreed_framings():
    """The canonical-check half references the agreed-framings file the founder
    fills — the mechanism that closes the loop with (b)."""
    text = _user_prompt_submit_text(_repo_settings())
    assert "agreed-framings.md" in text
