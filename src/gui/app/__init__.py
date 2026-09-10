"""wx GUI for chaddr."""

from __future__ import annotations

from pathlib import Path

import wx

from chaddr.gui.theme import install_default_gui_font
from chaddr.privilege import make_wx_password_prompt, set_gui_mode
from chaddr.profile import ensure_profile_dir

from .frame import AddressEditFrame


def run_gui(
    profiles: list[str] | None = None,
    cli_options: dict | None = None,
    proxy: str | None = None,
    config_path: Path | None = None,
    old_ip: str | None = None,
) -> None:
    ensure_profile_dir()
    app = wx.App(False)
    install_default_gui_font(10)
    frame = AddressEditFrame(profiles, cli_options, proxy, config_path, old_ip=old_ip)
    set_gui_mode(True, make_wx_password_prompt(frame))
    frame.Show()
    app.MainLoop()


__all__ = ["run_gui", "AddressEditFrame"]
