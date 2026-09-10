"""Package version from git describe (see scripts/git-version)."""

from __future__ import annotations

import subprocess
from pathlib import Path

_DEFAULT_VERSION = "0.0.0"
_REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from chaddr.buildconfig import VERSION as _BUILT_VERSION
except ImportError:
    _BUILT_VERSION = None


def git_describe_version(root: Path | None = None) -> str:
    """Return a PEP 440-friendly version string from ``git describe``."""
    root = root or _REPO_ROOT
    try:
        proc = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _DEFAULT_VERSION
    if proc.returncode != 0:
        return _DEFAULT_VERSION

    v = proc.stdout.strip()
    if v.startswith("v"):
        v = v[1:]
    if not v:
        return _DEFAULT_VERSION
    # No dotted component (e.g. commit hash only) -> 0.0.0-<describe>
    if "." not in v:
        v = f"0.0.0-{v}"
    return v


def get_version() -> str:
    """Installed package version, or git describe when running from a checkout."""
    if _BUILT_VERSION:
        return _BUILT_VERSION
    try:
        from importlib.metadata import version

        return version("chaddr")
    except Exception:
        return git_describe_version()


__version__ = get_version()
