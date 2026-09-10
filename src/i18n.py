"""gettext helpers for chaddr."""

from __future__ import annotations

import gettext
import locale
import os
from pathlib import Path

TEXT_DOMAIN = "chaddr"

# Always call through this function so `from chaddr.i18n import _` stays valid
# after init_i18n() swaps the underlying catalog.
_translation: gettext.NullTranslations = gettext.NullTranslations()


def _(message: str) -> str:
    return _translation.gettext(message)


def init_i18n(argv0: str = "") -> gettext.NullTranslations:
    """Install gettext translations for the process (fallback = English msgid)."""
    global _translation

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass

    localedir = os.environ.get("CHADDR_LOCALEDIR") or os.environ.get("ZEPHYR_LOCALEDIR")
    if not localedir:
        # Build-dir launcher: <build>/chaddr → <build>/po
        if argv0:
            build_po = Path(argv0).resolve().parent / "po"
            if (build_po / "zh_CN" / "LC_MESSAGES" / f"{TEXT_DOMAIN}.mo").is_file() or (
                build_po.is_dir() and any(build_po.glob("*/LC_MESSAGES/*.mo"))
            ):
                localedir = str(build_po)
        # Meson-injected build root (uninstalled)
        build_root = os.environ.get("CHADDR_BUILD_ROOT")
        if not localedir and build_root:
            candidate = Path(build_root) / "po"
            if candidate.is_dir():
                localedir = str(candidate)

    _translation = gettext.translation(TEXT_DOMAIN, localedir=localedir, fallback=True)
    _translation.install()
    return _translation
