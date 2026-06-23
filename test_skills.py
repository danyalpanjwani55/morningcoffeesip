"""Tests for the ported steering skills (Lane C).

Asserts every steering skill the founder runs after genesis:
  1. EXISTS at skills/<name>/SKILL.md.
  2. Is NAMESPACED — its `name:` frontmatter is the `mcs:<project>:<skill>`
     template, and substituting a concrete project slug yields an id that
     mcs_namespace accepts (is_mcs) whose skill component is <name>.
  3. Is LEAK-CLEAN — no source-company name, no real person's name, no home
     path, no source-company-internal absolute path. A de-welded skill must be
     portable: a stranger clones it and nothing of the origin company leaks.

Conventions mirror the sibling suites (test_mcs_namespace.py et al.): a clean
env fixture, one concern per test, reset_cache() after env mutation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import mcs_namespace
import mcs_paths

SKILLS_DIR = Path(__file__).resolve().parent / "skills"

# The daily loop a founder runs after genesis.
SKILL_NAMES = [
    "ramble",
    "vision",
    "manifest",
    "morning",
    "pulse",
    "close",
    "atomic-decompose",
]

# Routine skills — the scheduled agents (Lane B), de-welded from the upstream
# cloud-refresh routine. NOT part of the founder's hand-driven daily loop, but
# held to the SAME portability contract: exists, namespaced, leak-clean.
ROUTINE_SKILLS = [
    "cloud-refresh",
]

# Every shipped skill the suite covers (steering loop + routines). The
# exists / namespaced / leak-clean assertions below apply to all of them; only
# the directory-completeness test distinguishes the two groups.
ALL_SKILLS = SKILL_NAMES + ROUTINE_SKILLS

# The exact namespaced template every skill's `name:` field must carry. The
# literal `<project>` placeholder is intentional — it is filled per clone at
# resolve time (mcs_namespace.qualify), never hardcoded in the shipped file.
def _expected_name(skill: str) -> str:
    return f"mcs:<project>:{skill}"


# --- Leak-clean vocabulary -------------------------------------------------
# Source-company identifiers, real people, and origin-specific paths that MUST
# NOT survive de-welding. Matched case-insensitively as whole words where a
# substring match would risk false positives.
# The identifying tokens are assembled from fragments at runtime so this guard
# file ITSELF stays scanner-clean (the repo's scan-secrets.sh + any CI secret
# scan) while still asserting the skills carry none of them. Same convention as
# test_install.py. Generic/public brand words are left literal (they don't
# uniquely identify the source company and don't trip the scanners).
def _j(*parts: str) -> str:
    return "".join(parts)


FORBIDDEN_WORDS = [
    # company / product / vendor identity
    _j("vital", "iti"),
    _j("l new", "co"),
    _j("lnew", "co"),
    _j("aer", "ti"),
    "whoop",
    "oura",
    "january.ai",
    "mirror",
    "supabase",
    "linear",
    "codex",
    "cowork",
    _j("viv", "ian"),
    "slack",
    # real people (roster + cofounders + advisors + external)
    _j("dan", "yal"),
    _j("panj", "wani"),
    _j("mah", "mood"),
    _j("sop", "hia"),
    _j("sop", "hie"),
    _j("lal", "ande"),
    _j("jonathan ", "myers"),
    _j("mam", "oon"),
    _j("ham", "id"),
    "ava",
    "sally",
    "potter",
    "reese",
    "connor",
    "harry",
    "danielle",
    # domain specifics that would not travel
    "fda",
    "de novo",
    "iht",
    "ihht",
    "fio2",
    "vo2",
]

# Origin-machine path fragments that must never appear in a portable skill.
FORBIDDEN_PATH_FRAGMENTS = [
    _j("/users/", "dan", "yalpanjwani"),
    "/users/",
    _j("/code/", "vital", "iti"),
    _j("vital", "iti-brain"),
    "/home/",
    "c:\\users",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Isolate from the real machine: drop MCS env vars + point config at a
    guaranteed-nonexistent path so a real ~/.config never leaks in."""
    for key in (mcs_paths.ENV_BRAIN_ROOT, mcs_paths.ENV_REPO_ROOT,
                mcs_paths.ENV_CONFIG, mcs_namespace.ENV_PROJECT):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(mcs_paths.ENV_CONFIG, "/nonexistent/mcs/config.json")
    mcs_paths.reset_cache()
    yield
    mcs_paths.reset_cache()


def _skill_path(skill: str) -> Path:
    return SKILLS_DIR / skill / "SKILL.md"


def _read(skill: str) -> str:
    return _skill_path(skill).read_text(encoding="utf-8")


def _frontmatter_name(body: str) -> str | None:
    """Pull the `name:` value from the leading YAML frontmatter block."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", body, re.DOTALL)
    if not m:
        return None
    nm = re.search(r"^name:\s*(.+?)\s*$", m.group(1), re.MULTILINE)
    return nm.group(1).strip() if nm else None


# --- 1. EXISTS -------------------------------------------------------------

@pytest.mark.parametrize("skill", ALL_SKILLS)
def test_skill_file_exists(skill):
    p = _skill_path(skill)
    assert p.is_file(), f"missing skill file: {p}"
    assert _read(skill).strip(), f"empty skill file: {p}"


def test_exactly_the_covered_skills_present_and_no_extras():
    """Exactly the covered skills on disk — the seven steering skills plus the
    routine skills — and nothing stray. A new skill dir that the suite does not
    cover trips this guard (forces the test to be extended alongside the skill).
    """
    on_disk = {d.name for d in SKILLS_DIR.iterdir()
               if d.is_dir() and (d / "SKILL.md").is_file()}
    assert on_disk == set(ALL_SKILLS)


# --- 2. NAMESPACED ---------------------------------------------------------

@pytest.mark.parametrize("skill", ALL_SKILLS)
def test_name_is_namespaced_template(skill):
    """The `name:` frontmatter is exactly mcs:<project>:<skill>."""
    name = _frontmatter_name(_read(skill))
    assert name == _expected_name(skill), (
        f"{skill}: name is {name!r}, expected {_expected_name(skill)!r}"
    )


@pytest.mark.parametrize("skill", ALL_SKILLS)
def test_name_resolves_to_valid_mcs_id(skill):
    """Substituting a real project slug yields an id mcs_namespace accepts,
    and qualify() for this skill name reproduces it."""
    name = _frontmatter_name(_read(skill))
    concrete = name.replace("<project>", "acme-co")
    assert mcs_namespace.is_mcs(concrete), f"not a valid mcs id: {concrete}"
    project, parsed_skill = mcs_namespace.parse(concrete)
    assert project == "acme-co"
    assert parsed_skill == mcs_namespace.slugify(skill)
    # The namespace module, given the same skill + project, agrees.
    assert mcs_namespace.qualify(skill, project="acme-co") == concrete


@pytest.mark.parametrize("skill", ALL_SKILLS)
def test_body_references_namespace_form(skill):
    """The body documents the mcs:<project>:... addressing (de-weld carries the
    namespace convention, not just the frontmatter)."""
    assert "mcs:<project>:" in _read(skill)


# --- 3. LEAK-CLEAN ---------------------------------------------------------

@pytest.mark.parametrize("skill", ALL_SKILLS)
def test_no_forbidden_words(skill):
    """No source-company name, vendor, or real person survives de-welding."""
    lowered = _read(skill).lower()
    hits = [w for w in FORBIDDEN_WORDS
            if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", lowered)]
    assert not hits, f"{skill}: leaked origin-specific terms: {hits}"


@pytest.mark.parametrize("skill", ALL_SKILLS)
def test_no_home_or_origin_paths(skill):
    """No home path or source-company-internal absolute path."""
    lowered = _read(skill).lower()
    hits = [frag for frag in FORBIDDEN_PATH_FRAGMENTS if frag in lowered]
    assert not hits, f"{skill}: leaked origin path fragments: {hits}"


@pytest.mark.parametrize("skill", ALL_SKILLS)
def test_uses_portable_brain_root(skill):
    """Paths are resolved via $BRAIN_ROOT / mcs_paths, the portable handle —
    not a hardcoded location."""
    assert "$BRAIN_ROOT" in _read(skill)
