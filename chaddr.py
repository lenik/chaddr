#!/usr/bin/env python3
"""Address edit tool: manually change or reallocate profile-managed IP addresses."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if (ROOT / "chaddr").is_dir() and str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Fallbacks when this file was installed as /usr/bin/chaddr but the active
# interpreter (often a venv via /usr/bin/env) cannot see dist-packages.
for candidate in (
    Path("/usr/lib/python3/dist-packages"),
    Path(f"/usr/lib/python{sys.version_info.major}.{sys.version_info.minor}/dist-packages"),
    Path("/usr/local/lib/python3/dist-packages"),
):
    if (candidate / "chaddr").is_dir() and str(candidate) not in sys.path:
        sys.path.append(str(candidate))

import os

if sys.platform.startswith("linux") and "GTK_A11Y" not in os.environ:
    os.environ["GTK_A11Y"] = "none"

from chaddr.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
