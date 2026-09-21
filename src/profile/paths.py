"""Profile directory paths and helpers."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path


def _load_buildconfig():
    """Prefer installed ``chaddr.buildconfig``; else Meson builddir via CHADDR_BUILD_ROOT."""
    try:
        from chaddr import buildconfig as mod

        return mod
    except ImportError:
        pass
    build_root = os.environ.get("CHADDR_BUILD_ROOT", "").strip()
    if not build_root:
        return None
    path = Path(build_root) / "src" / "buildconfig.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("chaddr.buildconfig", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["chaddr.buildconfig"] = mod
    spec.loader.exec_module(mod)
    return mod


_buildconfig = _load_buildconfig()
# Project root is parents[2] of this file (src/profile/paths.py → project root).
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = _SOURCE_ROOT / "profile"
USER_PROFILE_DIR = Path.home() / ".config" / "chaddr" / "profile"
if _buildconfig is not None:
    PKGDATADIR = Path(getattr(_buildconfig, "PKGDATADIR", "") or _SOURCE_ROOT)
    _installed = getattr(_buildconfig, "PROFILE_DIR", "") or ""
    _INSTALLED_PROFILE_DIR = Path(_installed) if _installed else PKGDATADIR / "profile"
else:
    # Uninstalled / no Meson buildconfig yet: seed from the source tree only.
    PKGDATADIR = _SOURCE_ROOT
    _INSTALLED_PROFILE_DIR = PROFILE_DIR
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
