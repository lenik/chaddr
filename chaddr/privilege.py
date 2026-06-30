"""Privileged file writes via elevation (sudo, pkexec, gksudo, kdesudo)."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_PENDING_WRITES: ContextVar[list[tuple[Path, str, str]] | None] = ContextVar(
    "pending_writes",
    default=None,
)

_ELEVATORS_CP: tuple[list[str], ...] = (
    ["sudo", "-n", "cp"],
    ["sudo", "cp"],
    ["pkexec", "cp"],
    ["gksudo", "--", "cp"],
    ["kdesudo", "cp"],
)


@contextmanager
def batch_writes():
    """Defer privileged writes until exit; one elevation for the whole batch."""
    token = _PENDING_WRITES.set([])
    try:
        yield
        pending = _PENDING_WRITES.get()
        if pending:
            _flush_writes(pending)
    finally:
        _PENDING_WRITES.reset(token)


def write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write text to *path*, prompting for elevation if permission is denied."""
    pending = _PENDING_WRITES.get()
    if pending is not None:
        pending.append((path, content, encoding))
        return
    _write_text_now(path, content, encoding=encoding)


def _write_text_now(path: Path, content: str, *, encoding: str = "utf-8") -> None:
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


def _flush_writes(pending: list[tuple[Path, str, str]]) -> None:
    elevated: list[tuple[Path, Path]] = []
    temps: list[Path] = []
    for path, content, encoding in pending:
        try:
            path.write_text(content, encoding=encoding)
        except PermissionError:
            tmp_fd, tmp_name = tempfile.mkstemp(prefix="chaddr-", suffix=path.suffix or ".tmp")
            with os.fdopen(tmp_fd, "w", encoding=encoding) as tmp_file:
                tmp_file.write(content)
            temp_path = Path(tmp_name)
            elevated.append((temp_path, path))
            temps.append(temp_path)
    try:
        if elevated:
            _elevated_copy_many(elevated)
    finally:
        for temp_path in temps:
            temp_path.unlink(missing_ok=True)


def _elevated_copy_many(pairs: list[tuple[Path, Path]]) -> None:
    if len(pairs) == 1:
        _elevated_copy(pairs[0][0], pairs[0][1])
        return
    script = " && ".join(
        f"cp {shlex.quote(str(source))} {shlex.quote(str(dest))}"
        for source, dest in pairs
    )
    _elevated_shell(script, pairs[-1][1])


def _elevated_copy(source: Path, dest: Path) -> None:
    errors: list[str] = []
    for elev in _ELEVATORS_CP:
        cmd = elev + [str(source), str(dest)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except FileNotFoundError:
            continue
        if proc.returncode == 0:
            return
        err = (proc.stderr or proc.stdout or "").strip()
        errors.append(f"{' '.join(elev)}: {err or f'exit {proc.returncode}'}")

    detail = "; ".join(errors) if errors else "no elevation helper found (install sudo, pkexec, or gksudo)"
    raise PermissionError(f"permission denied: {dest} ({detail})")


def _elevated_shell(script: str, label: Path) -> None:
    errors: list[str] = []
    for prefix in (["sudo", "-n"], ["sudo"], ["pkexec"], ["gksudo", "--"], ["kdesudo"]):
        cmd = prefix + ["sh", "-c", script]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except FileNotFoundError:
            continue
        if proc.returncode == 0:
            return
        err = (proc.stderr or proc.stdout or "").strip()
        errors.append(f"{' '.join(prefix)}: {err or f'exit {proc.returncode}'}")

    detail = "; ".join(errors) if errors else "no elevation helper found (install sudo, pkexec, or gksudo)"
    raise PermissionError(f"permission denied: {label} ({detail})")
