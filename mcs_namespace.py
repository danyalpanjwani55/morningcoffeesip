"""Per-project skill namespace for MorningCoffeeSip. Every shipped skill is
addressed as ``mcs:<project_slug>:<skill>`` so a clone's skills never collide
with the user's other Claude Code skills, and two brains stay separate.

The project slug comes from (first hit wins): an explicit arg > $MCS_PROJECT >
config 'project_slug' > a sanitized form of the brain-root folder name >
"default"."""

from __future__ import annotations

import os
import re

import mcs_paths

PREFIX = "mcs"
ENV_PROJECT = "MCS_PROJECT"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(raw: str) -> str:
    """Lowercase, collapse non-alphanumerics to single hyphens, trim hyphens.
    Empty / all-symbol input -> 'default'."""
    s = _SLUG_RE.sub("-", raw.strip().lower()).strip("-")
    return s or "default"


def project_slug(explicit: str | None = None) -> str:
    """Resolve the project slug. Order: explicit > $MCS_PROJECT > config
    'project_slug' > slugify(brain_root().name) > 'default'."""
    if explicit:
        return slugify(explicit)
    if os.environ.get(ENV_PROJECT):
        return slugify(os.environ[ENV_PROJECT])
    cfg = mcs_paths._read_config().get("project_slug")
    if cfg:
        return slugify(cfg)
    return slugify(mcs_paths.brain_root().name)


def qualify(skill: str, *, project: str | None = None) -> str:
    """Return the fully-namespaced skill id ``mcs:<project>:<skill>``.
    ``skill`` is itself slugified so callers can pass a human label."""
    return f"{PREFIX}:{project_slug(project)}:{slugify(skill)}"


def parse(qualified: str) -> tuple[str, str]:
    """Inverse of qualify: 'mcs:acme:ramble' -> ('acme', 'ramble'). Raises
    ValueError if the id is not a well-formed mcs:-namespaced id."""
    parts = qualified.split(":")
    if len(parts) != 3 or parts[0] != PREFIX or not parts[1] or not parts[2]:
        raise ValueError(f"Not an mcs-namespaced skill id: {qualified!r}")
    return parts[1], parts[2]


def is_mcs(qualified: str) -> bool:
    """True iff the id is a well-formed mcs:-namespaced skill id."""
    try:
        parse(qualified)
        return True
    except ValueError:
        return False
