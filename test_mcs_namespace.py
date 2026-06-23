"""Tests for mcs_namespace (BUILD-SPEC-03 §2d). Each row of the spec table is
one test. reset_cache() is called after env/config mutation."""

from __future__ import annotations

from pathlib import Path

import pytest

import mcs_namespace
import mcs_paths


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (mcs_paths.ENV_BRAIN_ROOT, mcs_paths.ENV_REPO_ROOT,
                mcs_paths.ENV_CONFIG, mcs_namespace.ENV_PROJECT):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(mcs_paths.ENV_CONFIG, "/nonexistent/mcs/config.json")
    mcs_paths.reset_cache()
    yield
    mcs_paths.reset_cache()


def _write_config(tmp_path: Path, monkeypatch, body: str) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text(body, encoding="utf-8")
    monkeypatch.setenv(mcs_paths.ENV_CONFIG, str(cfg))
    mcs_paths.reset_cache()


def test_1_slugify_basic():
    assert mcs_namespace.slugify("Acme Co!") == "acme-co"


def test_2_slugify_empty_and_symbols():
    assert mcs_namespace.slugify("  ") == "default"
    assert mcs_namespace.slugify("***") == "default"


def test_3_qualify_explicit_project():
    assert mcs_namespace.qualify("ramble", project="Acme Co") == "mcs:acme-co:ramble"


def test_4_qualify_env_project(monkeypatch):
    monkeypatch.setenv(mcs_namespace.ENV_PROJECT, "Beta")
    mcs_paths.reset_cache()
    assert mcs_namespace.qualify("Ramble Skill") == "mcs:beta:ramble-skill"


def test_5_slug_from_brain_root_name(monkeypatch):
    monkeypatch.setenv(mcs_paths.ENV_BRAIN_ROOT, "/tmp/MyBrain")
    mcs_paths.reset_cache()
    assert mcs_namespace.project_slug() == "mybrain"


def test_6_slug_from_config(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, '{"project_slug": "from-cfg"}')
    assert mcs_namespace.project_slug() == "from-cfg"


def test_7_env_beats_config(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, '{"project_slug": "from-cfg"}')
    monkeypatch.setenv(mcs_namespace.ENV_PROJECT, "from-env")
    mcs_paths.reset_cache()
    assert mcs_namespace.project_slug() == "from-env"


def test_8_parse_roundtrip():
    assert mcs_namespace.parse("mcs:acme:ramble") == ("acme", "ramble")


def test_9_parse_rejects_malformed():
    for bad in ("ramble", "mcs:acme", "x:y:z"):
        with pytest.raises(ValueError):
            mcs_namespace.parse(bad)


def test_10_is_mcs():
    assert mcs_namespace.is_mcs("mcs:acme:ramble") is True
    assert mcs_namespace.is_mcs("ramble") is False
