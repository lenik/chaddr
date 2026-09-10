"""Profile directory paths and helpers."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

try:
    from chaddr.buildconfig import PROFILE_DIR as _INSTALLED_PROFILE_DIR_STR
except ImportError:
    _INSTALLED_PROFILE_DIR_STR = "/usr/share/chaddr/profile"

# Project root is parents[2] of this file (src/profile/paths.py → project root).
PROFILE_DIR = Path(__file__).resolve().parents[2] / "profile"
USER_PROFILE_DIR = Path.home() / ".config" / "chaddr" / "profile"
_INSTALLED_PROFILE_DIR = Path(_INSTALLED_PROFILE_DIR_STR)
_active_profile_dir: Path | None = None


def display_profile_path(path: Path) -> str:
    """Human-readable path with ~ for the home directory."""
    try:
        expanded = path.expanduser().resolve()
    except (OSError, RuntimeError):
        expanded = path.expanduser()
    home = Path.home()
    try:
        home_resolved = home.resolve()
        if expanded == home_resolved:
            return "~"
        relative = os.path.relpath(expanded, home_resolved)
        if not relative.startswith(".."):
            return f"~/{relative}".replace("\\", "/")
    except (OSError, RuntimeError, ValueError):
        pass
    return str(expanded).replace("\\", "/")


def format_profile_dir_label(path: Path | None = None) -> str:
    display = display_profile_path(path or get_profile_dir())
    if not display.endswith("/"):
        display += "/"
    return f"Profile: {display}"


def get_profile_dir() -> Path:
    if _active_profile_dir is not None:
        return _active_profile_dir
    env = os.environ.get("CHADDR_PROFILE_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    return USER_PROFILE_DIR


def ensure_profile_dir() -> Path:
    """Create the default profile directory and seed example profiles if empty."""
    root = get_profile_dir()
    root.mkdir(parents=True, exist_ok=True)
    if not any(item.is_file() and not item.name.startswith(".") for item in root.iterdir()):
        for source in (_INSTALLED_PROFILE_DIR, PROFILE_DIR):
            if not source.is_dir():
                continue
            for src in sorted(source.iterdir()):
                if not src.is_file() or src.name.startswith("."):
                    continue
                dest = root / src.name
                if not dest.exists():
                    shutil.copy2(src, dest)
    return root


def set_profile_dir(path: Path) -> None:
    global _active_profile_dir
    _active_profile_dir = path
