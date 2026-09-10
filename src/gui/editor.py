"""Open files in the system default text editor."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def open_in_system_editor(path: Path) -> None:
    """Launch *path* in the user's preferred text editor."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if editor:
        cmd = shlex.split(editor, posix=os.name != "nt")
        cmd.append(str(resolved))
        subprocess.Popen(cmd)
        return

    if sys.platform == "darwin":
        subprocess.Popen(["open", "-t", str(resolved)])
        return

    if os.name == "nt":
        os.startfile(str(resolved))  # type: ignore[attr-defined]
        return

    for candidate in ("xdg-open", "editor", "mousepad", "gedit", "kate", "nano", "vi"):
        prog = shutil.which(candidate)
        if prog:
            subprocess.Popen([prog, str(resolved)])
            return

    raise RuntimeError("No text editor found; set EDITOR or VISUAL")
