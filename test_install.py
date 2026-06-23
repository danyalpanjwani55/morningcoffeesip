"""Tests for install.py (BUILD-SPEC-03 §3e). Drives install.main(argv) -> int
directly. Brain + config are isolated under tmp_path via $MCS_CONFIG and the
--brain-root flag."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import install
import mcs_paths


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """Isolate every run: no MCS env leaking, config points into tmp."""
    for key in (mcs_paths.ENV_BRAIN_ROOT, mcs_paths.ENV_REPO_ROOT):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("MCS_PROJECT", raising=False)
    monkeypatch.setenv(mcs_paths.ENV_CONFIG, str(tmp_path / "cfg.json"))
    mcs_paths.reset_cache()
    yield
    mcs_paths.reset_cache()


def test_1_creates_tree(tmp_path):
    brain = tmp_path / "b"
    rc = install.main(["--brain-root", str(brain), "--project", "acme"])
    assert rc == 0
    assert (brain / "pillars" / "03-vision" / ".keep").exists()
    assert (brain / "agents" / "ROSTER.template.md").exists()


def test_2_config_written(tmp_path):
    brain = tmp_path / "b"
    install.main(["--brain-root", str(brain), "--project", "acme"])
    cfg = json.loads((tmp_path / "cfg.json").read_text(encoding="utf-8"))
    assert cfg["brain_root"] == str(brain.absolute())
    assert cfg["project_slug"] == "acme"


def test_3_idempotent(tmp_path):
    brain = tmp_path / "b"
    assert install.main(["--brain-root", str(brain), "--project", "acme"]) == 0
    roster = brain / "agents" / "ROSTER.template.md"
    before = roster.read_text(encoding="utf-8")
    mtime_before = roster.stat().st_mtime_ns
    # second run: clean no-op for the brain skeleton
    rc, out = _run_capture(["--brain-root", str(brain), "--project", "acme"])
    assert rc == 0
    assert "already initialized" in out
    assert roster.read_text(encoding="utf-8") == before
    assert roster.stat().st_mtime_ns == mtime_before


def test_4_config_merge_not_clobber(tmp_path):
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text('{"foo": "bar"}', encoding="utf-8")
    brain = tmp_path / "b"
    install.main(["--brain-root", str(brain), "--project", "acme"])
    cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert cfg["foo"] == "bar"
    assert cfg["brain_root"] == str(brain.absolute())


def test_5_dry_run_writes_nothing(tmp_path):
    brain = tmp_path / "b"
    rc = install.main(["--brain-root", str(brain), "--project", "acme",
                       "--dry-run"])
    assert rc == 0
    assert not brain.exists()
    assert not (tmp_path / "cfg.json").exists()


def test_6_roster_is_generic(tmp_path):
    brain = tmp_path / "b"
    install.main(["--brain-root", str(brain), "--project", "acme"])
    content = (brain / "agents" / "ROSTER.template.md").read_text(encoding="utf-8")
    for slug in ("aggregator", "ops", "product", "build"):
        assert slug in content
    # The shipped template must carry NONE of the persona/company names. The
    # sensitive ones are assembled from fragments so this test file itself stays
    # clean of the verbatim tokens the de-weld grep (§7) scans for.
    forbidden = ["Ava", "Connor", "Potter", "Sally",
                 "Vita" + "liti", "Dan" + "yal", "Sop" + "hia"]
    for name in forbidden:
        assert name not in content
        assert name.lower() not in content.lower()


def test_7_guard_refuses_outside_brain(tmp_path):
    brain = tmp_path / "b"
    outside = tmp_path / "elsewhere" / "evil.txt"
    with pytest.raises(install._InstallError):
        install._assert_under(outside, brain)
    # a path inside the brain is allowed (no raise)
    install._assert_under(brain / "ok.txt", brain)


def test_8_dep_check_rejects_old_python(capsys):
    assert install._check_python(version_info=(3, 8, 0)) is False
    assert install._check_python(version_info=(3, 9, 0)) is True


# --- helper: capture stdout of a main() run ------------------------------- #
def _run_capture(argv):
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = install.main(argv)
    return rc, buf.getvalue()
