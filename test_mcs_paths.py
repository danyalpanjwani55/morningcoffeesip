"""Tests for mcs_paths (BUILD-SPEC-03 §1d). Each row of the spec table is one
test. Env + config are mutated via monkeypatch + tmp_path, then reset_cache()
is called so the lazy config cache re-reads."""

from __future__ import annotations

from pathlib import Path

import pytest

import mcs_paths


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start each test from a clean slate: no MCS env vars, point $MCS_CONFIG
    at a guaranteed-nonexistent path so the real ~/.config never leaks in, and
    clear the cache before AND after."""
    for key in (mcs_paths.ENV_BRAIN_ROOT, mcs_paths.ENV_REPO_ROOT,
                mcs_paths.ENV_CONFIG):
        monkeypatch.delenv(key, raising=False)
    # default to a non-existent config so tests that don't set one are isolated
    monkeypatch.setenv(mcs_paths.ENV_CONFIG, "/nonexistent/mcs/config.json")
    mcs_paths.reset_cache()
    yield
    mcs_paths.reset_cache()


def _write_config(tmp_path: Path, monkeypatch, body: str) -> Path:
    cfg = tmp_path / "config.json"
    cfg.write_text(body, encoding="utf-8")
    monkeypatch.setenv(mcs_paths.ENV_CONFIG, str(cfg))
    mcs_paths.reset_cache()
    return cfg


def test_1_defaults_no_env_no_config():
    here = Path(__file__).resolve().parent
    assert mcs_paths.repo_root() == here
    assert mcs_paths.brain_root() == here / "brain"


def test_2_env_brain_root_absolute(monkeypatch):
    monkeypatch.setenv(mcs_paths.ENV_BRAIN_ROOT, "/tmp/x")
    mcs_paths.reset_cache()
    assert mcs_paths.brain_root() == Path("/tmp/x")


def test_3_env_repo_root_tilde_expands(monkeypatch):
    monkeypatch.setenv(mcs_paths.ENV_REPO_ROOT, "~/foo")
    mcs_paths.reset_cache()
    assert mcs_paths.repo_root() == Path.home() / "foo"


def test_4_config_brain_root(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, '{"brain_root": "/tmp/cfg"}')
    assert mcs_paths.brain_root() == Path("/tmp/cfg")


def test_5_env_beats_config(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, '{"brain_root": "/tmp/cfg"}')
    monkeypatch.setenv(mcs_paths.ENV_BRAIN_ROOT, "/tmp/from-env")
    mcs_paths.reset_cache()
    assert mcs_paths.brain_root() == Path("/tmp/from-env")


def test_6_explicit_beats_all(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, '{"brain_root": "/tmp/cfg"}')
    monkeypatch.setenv(mcs_paths.ENV_BRAIN_ROOT, "/tmp/from-env")
    mcs_paths.reset_cache()
    assert mcs_paths.brain_root("/tmp/explicit") == Path("/tmp/explicit")


def test_7_malformed_config_raises(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, "not json{{")
    with pytest.raises(ValueError):
        mcs_paths.brain_root()


def test_8_missing_config_file_no_raise(monkeypatch):
    monkeypatch.setenv(mcs_paths.ENV_CONFIG, "/definitely/not/here.json")
    mcs_paths.reset_cache()
    assert mcs_paths._read_config() == {}
    here = Path(__file__).resolve().parent
    assert mcs_paths.brain_root() == here / "brain"


def test_9_env_var_inside_value_expands(monkeypatch):
    monkeypatch.setenv(mcs_paths.ENV_BRAIN_ROOT, "$HOME/b")
    mcs_paths.reset_cache()
    assert mcs_paths.brain_root() == Path.home() / "b"
