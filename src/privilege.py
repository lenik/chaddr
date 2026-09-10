"""Privileged file writes via elevation (sudo, pkexec, gksudo, kdesudo)."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

_PENDING_WRITES: ContextVar[list[tuple[Path, str, str]] | None] = ContextVar(
    "pending_writes",
    default=None,
)

_gui_mode = False
_gui_password_prompt: Callable[[str], str | None] | None = None

_ELEVATORS_CP: tuple[list[str], ...] = (
    ["sudo", "-n", "cp"],
    ["sudo", "cp"],
    ["pkexec", "cp"],
    ["gksudo", "--", "cp"],
    ["kdesudo", "cp"],
)

_ELEVATORS_CP_GUI: tuple[list[str], ...] = (
    ["sudo", "-n", "cp"],
    ["pkexec", "cp"],
    ["gksudo", "--", "cp"],
    ["kdesudo", "cp"],
)

_SHELL_PREFIXES = (
    ["sudo", "-n"],
    ["sudo"],
    ["pkexec"],
    ["gksudo", "--"],
    ["kdesudo"],
)

_SHELL_PREFIXES_GUI = (
    ["sudo", "-n"],
    ["pkexec"],
    ["gksudo", "--"],
    ["kdesudo"],
)


def set_gui_mode(enabled: bool, password_prompt: Callable[[str], str | None] | None = None) -> None:
    """Enable GUI-friendly elevation (PolicyKit / password dialog instead of TTY sudo)."""
    global _gui_mode, _gui_password_prompt
    _gui_mode = enabled
    _gui_password_prompt = password_prompt


def _elevators_cp() -> tuple[list[str], ...]:
    return _ELEVATORS_CP_GUI if _gui_mode else _ELEVATORS_CP


def _shell_prefixes() -> tuple[list[str], ...]:
    return _SHELL_PREFIXES_GUI if _gui_mode else _SHELL_PREFIXES


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
    for elev in _elevators_cp():
        cmd = elev + [str(source), str(dest)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except FileNotFoundError:
            continue
        if proc.returncode == 0:
            return
        err = (proc.stderr or proc.stdout or "").strip()
        errors.append(f"{' '.join(elev)}: {err or f'exit {proc.returncode}'}")

    if _gui_mode and _try_sudo_stdin_copy(source, dest):
        return

    detail = "; ".join(errors) if errors else "no elevation helper found (install sudo, pkexec, or gksudo)"
    raise PermissionError(f"permission denied: {dest} ({detail})")


def _elevated_shell(script: str, label: Path) -> None:
    errors: list[str] = []
    for prefix in _shell_prefixes():
        cmd = prefix + ["sh", "-c", script]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except FileNotFoundError:
            continue
        if proc.returncode == 0:
            return
        err = (proc.stderr or proc.stdout or "").strip()
        errors.append(f"{' '.join(prefix)}: {err or f'exit {proc.returncode}'}")

    if _gui_mode and _try_sudo_stdin_shell(script):
        return

    detail = "; ".join(errors) if errors else "no elevation helper found (install sudo, pkexec, or gksudo)"
    raise PermissionError(f"permission denied: {label} ({detail})")


def _prompt_password(message: str) -> str | None:
    prompt = _gui_password_prompt
    if prompt is None:
        return None
    try:
        return prompt(message)
    except Exception:
        return None


def _try_sudo_stdin_copy(source: Path, dest: Path) -> bool:
    password = _prompt_password(f"Authentication required to write:\n{dest}")
    if not password:
        return False
    try:
        proc = subprocess.run(
            ["sudo", "-S", "cp", str(source), str(dest)],
            input=password + "\n",
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        return False
    return proc.returncode == 0


def _try_sudo_stdin_shell(script: str) -> bool:
    password = _prompt_password("Authentication required for privileged file updates.")
    if not password:
        return False
    try:
        proc = subprocess.run(
            ["sudo", "-S", "sh", "-c", script],
            input=password + "\n",
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        return False
    return proc.returncode == 0


def make_wx_password_prompt(parent: Any) -> Callable[[str], str | None]:
    """Build a thread-safe wx password prompt bound to *parent*."""

    def prompt(message: str) -> str | None:
        import wx

        result: dict[str, str | None] = {"password": None}
        done = threading.Event()

        def _show() -> None:
            try:
                dialog = wx.PasswordEntryDialog(
                    parent,
                    message,
                    "sudo password",
                )
                try:
                    if dialog.ShowModal() == wx.ID_OK:
                        result["password"] = dialog.GetValue()
                finally:
                    dialog.Destroy()
            finally:
                done.set()

        if wx.IsMainThread():
            _show()
        else:
            wx.CallAfter(_show)
            done.wait(timeout=300)
        return result["password"]

    return prompt
