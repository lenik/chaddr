#!/usr/bin/env python3
"""Address edit tool: manually change or reallocate profile-managed IP addresses."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if (ROOT / "chaddr").is_dir() and str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chaddr.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
