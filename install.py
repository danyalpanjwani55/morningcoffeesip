"""MorningCoffeeSip installer / scaffolder (BUILD-SPEC-03 §3, closes B12).

One command from a fresh ``git clone`` to a working, empty brain:
check deps -> write/merge config -> scaffold the empty pillar + base-roster
template tree -> namespace whatever skills have been ported into ``skills/`` ->
merge the pre-response discipline hook into the user's ``~/.claude/settings.json``
-> print the next step (run genesis).

Run it::

    python3 install.py [--brain-root PATH] [--project SLUG] [--force] [--dry-run]

Stdlib-only. Writes ONLY under the resolved brain root, the config dir, and the
user's ``~/.claude`` dir (the hook-merge step, below); it reads ``skills/`` but
never writes a skill file (skills are ported by a separate lane). It does NOT run
``git init``, does NOT create ``.gitignore``, does NOT delete anything, and does
NOT touch the network. Those are operator-gated (see BUILD-SPEC-03 §9
GATED-FOR-OPERATOR).

Idempotent: a second run with no flags is a clean no-op (the skeleton is kept,
the config is a merge, the skill manifest is rebuilt only when ``skills/`` has
changed). ``--force`` overwrites the skeleton + rewrites the manifest;
``--dry-run`` prints what would happen and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import mcs_namespace
import mcs_paths

MIN_PYTHON = (3, 9)

# The generic 4-pillar layout (pillar-structure-v4), domain-agnostic names.
_PILLAR_DIRS = (
    "pillars/01-interactions",
    "pillars/02-people",
    "pillars/03-vision",
    "pillars/04-research",
    "out",
    "archive",
)

# Where the namespaced-skill manifest lands inside the brain (Type-1, FOR-AI:
# the agent runner reads this to know each skill's namespaced id). A second copy
# is stamped into the config dir so a tool that only has the config path can find
# it without knowing the brain root.
_SKILL_MANIFEST_REL = "agents/skills.manifest.json"
_SKILL_MANIFEST_CFG = "skills.manifest.json"

# The repo ships its own Claude Code settings carrying the pre-response hook
# (route-first + canonical-check). The installer merges that hook into the clone
# user's own ``~/.claude/settings.json`` so the discipline fires in THEIR Claude
# Code, not just inside this repo's checkout.
_REPO_SETTINGS_REL = ".claude/settings.json"
_USER_SETTINGS_REL = "settings.json"  # under the user's ~/.claude dir

_BRAIN_README = """\
# Your company brain

This directory is your company brain. Right now it is an empty skeleton.

The **genesis engine** reads your connected sources (email, chat, files,
calendar, code) and fills these pillars: who your people are, what the company
is trying to do, and since inception — then proposes your vision, your
meta-initiatives, and an agent roster for you to ratify.

- `pillars/` — the four knowledge pillars genesis populates with cited facts.
- `agents/ROSTER.template.md` — your starting agent roster (edit or replace).
- `agents/skills.manifest.json` — the namespaced id of every skill the installer
  found in `skills/` (rebuilt each install; empty until skills are ported).
- `out/` — genesis writes its draft proposals here (nothing auto-applies).
- `archive/` — superseded facts move here (archive-don't-delete).

Next: connect your sources, then run genesis.
"""

_ROSTER_TEMPLATE = """\
# Agent roster (template)

This is your starting roster. Genesis proposes the company-specific additions
from your data — these four are illustrative slots to edit or replace.

| Slug | Domain (edit me) | What this agent owns |
|------|------------------|----------------------|
| aggregator | cross-company orchestration | composes the company-wide view; routes work |
| ops        | operations / business    | finance, vendors, process, logistics |
| product    | product / what-we-build  | the product thesis, roadmap, positioning |
| build      | engineering / how-we-build | the codebase, technical delivery |

> Genesis will propose more agents (with cited evidence) at the review step.
> Ratify, edit, or reject each before it becomes real.
"""


class _InstallError(RuntimeError):
    """A recoverable install failure (printed, mapped to a non-zero exit)."""


def _check_python(version_info=None) -> bool:
    """Assert the running interpreter meets the floor. Prints the interpreter
    path + version, and a heads-up if this build's ``pyexpat`` is broken (some
    newer Homebrew builds ship a broken one, which stops ``pip``/``pytest`` from
    starting — the install itself is stdlib-only and still works). Returns True
    if OK, False (after printing the error) if too old. ``version_info`` is
    injectable for tests."""
    vi = version_info if version_info is not None else sys.version_info
    print(f"interpreter: {sys.executable} (Python "
          f"{vi[0]}.{vi[1]}.{vi[2] if len(vi) > 2 else 0})")
    if tuple(vi[:2]) < MIN_PYTHON:
        print(
            f"ERROR: MorningCoffeeSip needs Python "
            f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ (everything is stdlib). "
            f"You are on {vi[0]}.{vi[1]}. Re-run with a newer python3.",
            file=sys.stderr,
        )
        return False
    if version_info is None and _pyexpat_broken():
        print(
            "WARNING: this Python's XML parser (pyexpat) is broken — a known "
            "issue on some Homebrew builds. The install is stdlib-only and "
            "still works, but pip/pytest may fail to start on this "
            "interpreter. If you hit that, use the system python instead "
            "(e.g. /usr/bin/python3).",
        )
    return True


def _pyexpat_broken() -> bool:
    """True if importing ``pyexpat`` fails on this interpreter (the broken
    Homebrew build). Pure-probe: catches ImportError only; never raises."""
    try:
        import pyexpat  # noqa: F401
        return False
    except ImportError:
        return True


def _assert_under(path: Path, *roots: Path) -> None:
    """Guard: refuse any write whose resolved path escapes ALL of ``roots``.

    Mirrors the genesis pipeline's ``_assert_under_out``. The installer may
    write only under the brain root or the config dir."""
    real_path = os.path.realpath(path)
    for root in roots:
        real_root = os.path.realpath(root)
        if real_path == real_root or real_path.startswith(real_root + os.sep):
            return
    raise _InstallError(f"Refusing write outside allowed roots: {path}")


def _write_config(config_file: Path, brain: Path, project: str,
                  *, dry_run: bool) -> None:
    """Merge brain_root + project_slug into the config JSON, never clobbering
    unrelated keys."""
    existing: dict = {}
    if config_file.is_file():
        try:
            loaded = json.loads(config_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except json.JSONDecodeError:
            existing = {}  # a malformed file is overwritten with a clean one
    existing["brain_root"] = str(brain)
    existing["project_slug"] = project
    body = json.dumps(existing, indent=2, sort_keys=True) + "\n"
    if dry_run:
        print(f"[dry-run] would write config {config_file}:\n{body}")
        return
    config_file.parent.mkdir(parents=True, exist_ok=True)
    _assert_under(config_file, config_file.parent)
    config_file.write_text(body, encoding="utf-8")
    print(f"wrote config: {config_file}")


def _scaffold(brain: Path, *, force: bool, dry_run: bool) -> None:
    """Create the empty brain skeleton under ``brain``. Idempotent: never
    overwrites an existing file unless ``force``. Logs every dir/file."""
    files: list[tuple[Path, str]] = [
        (brain / "README.md", _BRAIN_README),
        (brain / "agents" / "ROSTER.template.md", _ROSTER_TEMPLATE),
    ]
    for rel in _PILLAR_DIRS:
        files.append((brain / rel / ".keep", ""))

    for path, content in files:
        _assert_under(path, brain)
        if dry_run:
            print(f"[dry-run] would create {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not force:
            print(f"exists, kept: {path}")
            continue
        path.write_text(content, encoding="utf-8")
        print(f"created: {path}")


def _discover_skills(skills_dir: Path) -> list[str]:
    """Read ``skills/`` (READ-ONLY) and return the sorted skill names found.

    Skills are ported by a separate lane; this installer only namespaces what is
    already there. A skill is recognized as either layout the agent runner uses:
      - a directory with a ``SKILL.md`` inside (``skills/<name>/SKILL.md``), or
      - a flat markdown file (``skills/<name>.md``).
    Returns ``[]`` when ``skills/`` is absent or holds nothing recognizable —
    never raises, never writes."""
    if not skills_dir.is_dir():
        return []
    names: set[str] = set()
    for child in skills_dir.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_dir() and (child / "SKILL.md").is_file():
            names.add(child.name)
        elif child.is_file() and child.suffix == ".md" and child.stem:
            names.add(child.stem)
    return sorted(names)


def _build_skill_manifest(skill_names: list[str], project: str) -> dict:
    """Map each discovered skill name to its namespaced id. Type-1 payload."""
    return {
        "project_slug": project,
        "prefix": mcs_namespace.PREFIX,
        "skills": {
            name: mcs_namespace.qualify(name, project=project)
            for name in skill_names
        },
    }


def _install_skills(skills_dir: Path, brain: Path, config_dir: Path,
                    project: str, *, force: bool, dry_run: bool) -> int:
    """Namespace the ported skills: discover them in ``skills/`` (read-only) and
    write a manifest mapping each to ``mcs:<project>:<skill>``. Returns the count
    of skills namespaced. Idempotent: only rewrites a manifest whose content
    changed (or when ``force``). Writes ONLY the manifest, under brain + config
    dir — never a skill file."""
    skill_names = _discover_skills(skills_dir)
    manifest = _build_skill_manifest(skill_names, project)
    body = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

    if not skill_names:
        # Honest no-op: skills are ported by a later lane. We still surface the
        # state so the user knows the step ran and found nothing to do.
        if skills_dir.is_dir():
            print(f"skills: none recognized in {skills_dir} "
                  f"(nothing to namespace yet)")
        else:
            print(f"skills: no skills/ dir yet at {skills_dir} "
                  f"(skills are ported later; nothing to namespace)")

    targets = (brain / _SKILL_MANIFEST_REL, config_dir / _SKILL_MANIFEST_CFG)
    for target in targets:
        if dry_run:
            print(f"[dry-run] would write skill manifest {target} "
                  f"({len(skill_names)} skills)")
            continue
        root = brain if target == targets[0] else config_dir
        _assert_under(target, root)
        if target.is_file() and not force \
                and target.read_text(encoding="utf-8") == body:
            print(f"skill manifest unchanged: {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        print(f"wrote skill manifest: {target} ({len(skill_names)} skills)")

    if skill_names:
        for name in skill_names:
            print(f"  namespaced skill: {name} -> {manifest['skills'][name]}")
    return len(skill_names)


def _user_claude_dir() -> Path:
    """The clone user's Claude Code config dir (``~/.claude``). Injected by the
    installer step so tests can point it at a tmp dir; expanded + absolute, NOT
    required to exist (the step creates it)."""
    return (Path.home() / ".claude").expanduser().absolute()


def _merge_hooks(into: dict, repo_settings: dict) -> bool:
    """Merge the repo's ``hooks`` into the user's settings dict IN PLACE, without
    clobbering the user's unrelated keys or duplicating a hook entry that is
    already present verbatim. Returns True iff anything changed.

    Shape per Claude Code: ``hooks[<EventName>]`` is a list of matcher-groups;
    we append each repo group the user does not already carry (by value)."""
    repo_hooks = repo_settings.get("hooks")
    if not isinstance(repo_hooks, dict):
        return False
    user_hooks = into.setdefault("hooks", {})
    if not isinstance(user_hooks, dict):
        # The user's `hooks` is some non-object — refuse to guess; leave it.
        return False
    changed = False
    for event, groups in repo_hooks.items():
        if not isinstance(groups, list):
            continue
        existing = user_hooks.setdefault(event, [])
        if not isinstance(existing, list):
            continue
        for group in groups:
            if group not in existing:  # value-equality dedup => idempotent
                existing.append(group)
                changed = True
    return changed


def _install_hook(repo: Path, user_claude_dir: Path, *,
                  force: bool, dry_run: bool) -> bool:
    """Merge the repo's pre-response hook into the user's ``~/.claude/
    settings.json``. Reads the repo's ``.claude/settings.json`` (read-only) and
    deep-merges its ``hooks`` block into the user's settings, never clobbering
    the user's other keys and never duplicating an already-present hook. Writes
    ONLY the user's settings file, guarded under ``user_claude_dir``. Idempotent:
    a re-run with the hook already there is a no-op (unless ``force`` rewrites).
    Returns True iff the user's file was written."""
    repo_settings_file = repo / _REPO_SETTINGS_REL
    if not repo_settings_file.is_file():
        # The repo did not ship a settings file — nothing to install. Honest,
        # surfaced no-op (matches _install_skills' empty case).
        print(f"hook: no {_REPO_SETTINGS_REL} in repo — nothing to install")
        return False
    try:
        repo_settings = json.loads(
            repo_settings_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise _InstallError(
            f"repo {_REPO_SETTINGS_REL} is not valid JSON: {e}") from e

    user_settings_file = user_claude_dir / _USER_SETTINGS_REL
    existing: dict = {}
    if user_settings_file.is_file():
        try:
            loaded = json.loads(user_settings_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except json.JSONDecodeError:
            existing = {}  # a malformed user file is replaced with a clean one

    merged = dict(existing)  # don't mutate the read-back; build the new doc
    changed = _merge_hooks(merged, repo_settings)
    if not changed and not force and user_settings_file.is_file():
        print(f"hook present, unchanged: {user_settings_file}")
        return False

    body = json.dumps(merged, indent=2, sort_keys=True) + "\n"
    if dry_run:
        print(f"[dry-run] would merge pre-response hook into "
              f"{user_settings_file}")
        return False
    _assert_under(user_settings_file, user_claude_dir)
    user_settings_file.parent.mkdir(parents=True, exist_ok=True)
    user_settings_file.write_text(body, encoding="utf-8")
    print(f"installed pre-response hook -> {user_settings_file}")
    return True


def _brain_already_initialized(brain: Path) -> bool:
    """A pre-existing, non-empty brain dir (its marker README present) means
    'already initialized'."""
    return (brain / "README.md").is_file()


def _print_next_step(brain: Path, project: str, skill_count: int) -> None:
    repo = mcs_paths.repo_root()
    print("-" * 60)
    print("Brain scaffolded. Next steps:")
    print("  1. Connect your sources, then run genesis:")
    print(f"       python3 {repo / 'genesis' / 'genesis_pipeline.py'}")
    print(f"     (that demo runs the full pass and writes drafts to "
          f"{brain / 'out'})")
    if skill_count:
        print(f"  2. {skill_count} skill(s) namespaced as "
              f"{mcs_namespace.PREFIX}:{project}:<skill> "
              f"(see {brain / _SKILL_MANIFEST_REL}).")
    else:
        print(f"  2. No skills ported yet. Once they land in "
              f"{repo / 'skills'}, re-run install to namespace them as "
              f"{mcs_namespace.PREFIX}:{project}:<skill>.")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Scaffold a fresh MorningCoffeeSip company brain.",
    )
    parser.add_argument("--brain-root", default=None,
                        help="where the brain lives (default: <repo>/brain)")
    parser.add_argument("--project", default=None,
                        help="project slug for the skill namespace")
    parser.add_argument("--force", action="store_true",
                        help="overwrite existing skeleton files + manifest")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would happen; write nothing")
    args = parser.parse_args(argv)

    # 1. Dep check.
    if not _check_python():
        return 1

    # 2. Resolve targets.
    brain = mcs_paths.brain_root(args.brain_root)
    project = mcs_namespace.project_slug(args.project)
    config_file = mcs_paths.config_path()
    skills_dir = mcs_paths.repo_root() / "skills"
    print(f"brain root : {brain}")
    print(f"project    : {project}")

    skill_count = 0
    try:
        # 3. Config write/merge.
        _write_config(config_file, brain, project, dry_run=args.dry_run)

        # 4. Scaffold (idempotent). A pre-existing brain without --force is a
        #    clean no-op (still re-writes config above, which is a merge).
        if (not args.dry_run and not args.force
                and _brain_already_initialized(brain)):
            print(f"already initialized: {brain} (use --force to overwrite)")
        else:
            _scaffold(brain, force=args.force, dry_run=args.dry_run)

        # 5. Namespace whatever skills have been ported (read-only over
        #    skills/). Always runs — it must work both on the first install and
        #    on a re-run after a later lane drops skills in. The manifest write
        #    is itself idempotent (unchanged content => skipped).
        skill_count = _install_skills(
            skills_dir, brain, config_file.parent, project,
            force=args.force, dry_run=args.dry_run)

        # 6. Merge the pre-response discipline hook (route-first + canonical-
        #    check) into the user's own ~/.claude/settings.json, so it fires in
        #    their Claude Code, not only inside this repo. Idempotent merge that
        #    never clobbers the user's other settings.
        _install_hook(
            mcs_paths.repo_root(), _user_claude_dir(),
            force=args.force, dry_run=args.dry_run)
    except _InstallError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"ERROR: write failed: {e}", file=sys.stderr)
        return 1

    # 7. Next step.
    if not args.dry_run:
        _print_next_step(brain, project, skill_count)
    else:
        print("[dry-run] no changes written.")

    # 7. Done.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
