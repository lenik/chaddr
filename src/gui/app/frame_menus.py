"""File/edit/view menu handlers mixin for AddressEditFrame."""

from __future__ import annotations

from pathlib import Path

import wx

try:
    import wx.stc as stc
except ImportError:  # pragma: no cover
    stc = None

from chaddr import __version__
from chaddr.config import load_config, resolve_client_ip
from chaddr.gui.editor import open_in_system_editor
from chaddr.gui.highlighter import setup_styles
from chaddr.gui.theme import apply_theme
from chaddr.profile import display_profile_path, get_profile_dir
from chaddr.proxy import restore_proxy_env

from .ids import ID_SYNTAX_HIGHLIGHT
from .prefs import PreferencesDialog


class FileMenuMixin:
    def _on_load_config(self, _evt) -> None:
        dialog = wx.FileDialog(
            self,
            "Load Config",
            wildcard="JSON config (*.json)|*.json|Config (*)|*|All files (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        path = Path(dialog.GetPath())
        dialog.Destroy()
        options, proxy, _ = load_config(str(path))
        self.config_path = path
        self.cli_options.update(options)
        if proxy:
            self.apply_proxy(proxy)
        resolve_client_ip(self.cli_options, self.proxy, self.config_path, self.logger)
        self._update_status_bar()
        self.logger.info("Loaded config %s", path)

    def _on_edit_profiles(self, _evt) -> None:
        profiles = self._selected_profiles()
        if not profiles:
            wx.MessageBox(
                self,
                "Select a profile to open in a text editor.",
                "Edit Profile",
                wx.OK | wx.ICON_INFORMATION,
            )
            return

        root = get_profile_dir()
        opened = 0
        errors: list[str] = []
        for name in profiles:
            path = root / name
            try:
                open_in_system_editor(path)
                opened += 1
            except Exception as exc:
                errors.append(f"{name}: {exc}")

        if opened:
            self.logger.info(
                "Opened %d profile(s) in text editor from %s",
                opened,
                display_profile_path(root),
            )
        if errors:
            wx.MessageBox(
                self,
                "\n".join(errors),
                "Edit Profile",
                wx.OK | wx.ICON_ERROR,
            )

    def _on_preferences(self, _evt) -> None:
        dialog = PreferencesDialog(self, self.proxy, self.config_path)
        if dialog.ShowModal() == wx.ID_OK:
            self.apply_proxy(dialog.get_proxy(), save_to_config=False)
        dialog.Destroy()

    def _on_toggle_syntax(self, _evt) -> None:
        self.syntax_highlight = self.GetMenuBar().IsChecked(ID_SYNTAX_HIGHLIGHT)
        if stc is not None:
            for ctrl in self._log_ctrls.values():
                setup_styles(ctrl)
            for ctrl in self._diag_ctrls.values():
                setup_styles(ctrl)

    def _set_theme(self, theme_name: str) -> None:
        self.theme_name = theme_name
        self._apply_theme()

    def _apply_theme(self) -> None:
        widgets = {
            "panel": self._panel,
            "left_panel": self._left_panel,
            "right_panel": self._right_panel,
        }
        if self._log_ctrls:
            widgets["log_ctrl"] = next(iter(self._log_ctrls.values()))
        if self._diag_ctrls:
            widgets["summary_ctrl"] = next(iter(self._diag_ctrls.values()))
        apply_theme(self, self.theme_name, widgets)
        if stc is not None:
            for ctrl in self._log_ctrls.values():
                setup_styles(ctrl)
            for ctrl in self._diag_ctrls.values():
                setup_styles(ctrl)

    def _on_about(self, _evt) -> None:
        wx.MessageBox(
            f"chaddr {__version__}\n\n"
            "Change or reallocate IP addresses defined in profile files.\n\n"
            f"Profile dir: {display_profile_path(get_profile_dir())}/\n"
            f"Config: {self.config_path or '(none)'}",
            "About chaddr",
            wx.OK | wx.ICON_INFORMATION,
        )

    def _on_close(self, evt) -> None:
        restore_proxy_env(self._proxy_backup)
        evt.Skip()
