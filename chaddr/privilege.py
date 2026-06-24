"""Privileged file writes via GUI elevation (pkexec, gksudo, kdesudo)."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

_ELEVATORS: tuple[list[str], ...] = (
    ["pkexec", "cp"],
    ["gksudo", "--", "cp"],
    ["kdesudo", "cp"],
)


def write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write text to *path*, prompting for elevation if permission is denied."""
    try:
        path.write_text(content, encoding=encoding)
        return
    except PermissionError:
        pass

    tmp_fd, tmp_name = tempfile.mkstemp(prefix="chaddr-", suffix=path.suffix or ".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding=encoding) as tmp_file:
            tmp_file.write(content)
        _elevated_copy(Path(tmp_name), path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def _elevated_copy(source: Path, dest: Path) -> None:
    errors: list[str] = []
    for elev in _ELEVATORS:
        cmd = elev + [str(source), str(dest)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except FileNotFoundError:
            continue
        if proc.returncode == 0:
            return
        err = (proc.stderr or proc.stdout or "").strip()
        errors.append(f"{' '.join(elev)}: {err or f'exit {proc.returncode}'}")

    detail = "; ".join(errors) if errors else "no elevation helper found (install pkexec or gksudo)"
    raise PermissionError(f"permission denied: {dest} ({detail})")
