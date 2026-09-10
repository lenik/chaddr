"""gettext helpers for chaddr."""

from __future__ import annotations

import gettext
import locale
import os
from pathlib import Path

TEXT_DOMAIN = "chaddr"

# Bound after init_i18n(); identity until then.
_ = gettext.gettext


def init_i18n(argv0: str = "") -> gettext.NullTranslations:
    """Install gettext translations for the process (fallback = English msgid)."""
    global _

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass

    localedir = os.environ.get("CHADDR_LOCALEDIR") or os.environ.get("ZEPHYR_LOCALEDIR")
    if not localedir and argv0 and "/" in argv0:
        build_po = Path(argv0).resolve().parent / "po"
        if build_po.is_dir():
            localedir = str(build_po)

    trans = gettext.translation(TEXT_DOMAIN, localedir=localedir, fallback=True)
    trans.install()
    _ = trans.gettext
    return trans
