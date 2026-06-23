"""Path resolver for MorningCoffeeSip. ONE place every component asks 'where is
the brain root / repo root', so the same code runs on any machine. Resolution
order (first hit wins), for both BRAIN_ROOT and REPO_ROOT:
    1. an explicit argument passed to the function
    2. the environment variable ($MCS_BRAIN_ROOT / $MCS_REPO_ROOT)
    3. a value in the config file (~/.config/morningcoffeesip/config.json, or
       $MCS_CONFIG if set)
    4. a sane default (REPO_ROOT = the dir containing this file; BRAIN_ROOT =
       $REPO_ROOT/brain)
No network, no writes on import. Reading the config is lazy + cached."""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path

ENV_BRAIN_ROOT = "MCS_BRAIN_ROOT"
ENV_REPO_ROOT = "MCS_REPO_ROOT"
ENV_CONFIG = "MCS_CONFIG"


# Default config location (XDG-style; overridable via $MCS_CONFIG).
def _default_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "morningcoffeesip" / "config.json"


def config_path() -> Path:
    """The resolved config file path ($MCS_CONFIG wins, else the XDG default)."""
    override = os.environ.get(ENV_CONFIG)
    return Path(override) if override else _default_config_path()


@functools.lru_cache(maxsize=1)
def _read_config() -> dict[str, str]:
    """Load the config JSON if it exists; else {}. Never raises on a missing
    file; raises ValueError only on a present-but-malformed file."""
    p = config_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed MCS config at {p}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"MCS config at {p} must be a JSON object.")
    return {str(k): str(v) for k, v in data.items()}


def repo_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the repo root. Order: explicit arg > $MCS_REPO_ROOT > config
    'repo_root' > the directory containing this file. Returned absolute +
    expanded (~ and env vars), NOT required to exist."""
    return _resolve(explicit, ENV_REPO_ROOT, "repo_root",
                    default=Path(__file__).resolve().parent)


def brain_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the brain root. Order: explicit arg > $MCS_BRAIN_ROOT > config
    'brain_root' > $REPO_ROOT/brain. Returned absolute + expanded, NOT required
    to exist (the installer creates it)."""
    return _resolve(explicit, ENV_BRAIN_ROOT, "brain_root",
                    default=repo_root() / "brain")


def _resolve(explicit, env_key: str, config_key: str, *, default: Path) -> Path:
    if explicit is not None:
        return _norm(explicit)
    if os.environ.get(env_key):
        return _norm(os.environ[env_key])
    cfg = _read_config().get(config_key)
    if cfg:
        return _norm(cfg)
    return _norm(default)


def _norm(p: str | os.PathLike[str]) -> Path:
    """Expand ~ and $VARS, then make absolute. Does not resolve symlinks or
    require existence (so it works before the installer has run)."""
    s = os.path.expandvars(os.fspath(p))
    return Path(s).expanduser().absolute()


def reset_cache() -> None:
    """Clear the config cache (tests set env/config then re-resolve)."""
    _read_config.cache_clear()
