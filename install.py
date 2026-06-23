"""MorningCoffeeSip installer / scaffolder (BUILD-SPEC-03 §3, closes B12).

One command from a fresh ``git clone`` to a working, empty brain:
check deps -> write/merge config -> scaffold the empty pillar + base-roster
template tree -> print the next step.

Run it::

    python3 install.py [--brain-root PATH] [--project SLUG] [--force] [--dry-run]

Stdlib-only. Writes ONLY under the resolved brain root and the config dir; it
does NOT run ``git init``, does NOT create ``.gitignore``, does NOT delete
anything, and does NOT touch the network. Those are operator-gated (see
BUILD-SPEC-03 §9 GATED-FOR-OPERATOR).
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

_BRAIN_README = """\
# Your company brain

This directory is your company brain. Right now it is an empty skeleton.

The **genesis engine** reads your connected sources (email, chat, files,
calendar, code) and fills these pillars: who your people are, what the company
is trying to do, and since inception — then proposes your vision, your
meta-initiatives, and an agent roster for you to ratify.

- `pillars/` — the four knowledge pillars genesis populates with cited facts.
- `agents/ROSTER.template.md` — your starting agent roster (edit or replace).
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
    path + version. Returns True if OK, False (after printing the error) if too
    old. ``version_info`` is injectable for tests."""
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


def _brain_already_initialized(brain: Path) -> bool:
    """A pre-existing, non-empty brain dir (its marker README present) means
    'already initialized'."""
    return (brain / "README.md").is_file()


def _print_next_step(brain: Path, project: str) -> None:
    repo = mcs_paths.repo_root()
    print("-" * 60)
    print("Brain scaffolded. Next steps:")
    print(f"  1. Connect your sources, then run genesis:")
    print(f"       python3 {repo / 'genesis' / 'genesis_pipeline.py'}")
    print(f"     (that demo runs the full pass and writes drafts to "
          f"{brain / 'out'})")
    print(f"  2. Your skills are namespaced as: "
          f"{mcs_namespace.PREFIX}:{project}:<skill>")
    print(f"     e.g. {mcs_namespace.qualify('ramble', project=project)}")


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
                        help="overwrite existing skeleton files")
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
    print(f"brain root : {brain}")
    print(f"project    : {project}")

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
    except _InstallError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"ERROR: write failed: {e}", file=sys.stderr)
        return 1

    # 5. Next step.
    if not args.dry_run:
        _print_next_step(brain, project)
    else:
        print("[dry-run] no changes written.")

    # 6. Done.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
