"""Preferences dialog (duck-types parent to avoid circular import with frame)."""

from __future__ import annotations

from pathlib import Path

import wx

from chaddr.config import CONFIG_FILENAME
from chaddr.gui.theme import mono_font, ui_font


class PreferencesDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, proxy: str | None, config_path: Path | None) -> None:
        super().__init__(parent, title="Preferences", size=(500, 240))
        self.config_path = config_path
        panel = wx.Panel(self)
        panel.SetFont(ui_font(10))
        sizer = wx.BoxSizer(wx.VERTICAL)

        proxy_box = wx.StaticBox(panel, label="Proxy")
        proxy_sizer = wx.StaticBoxSizer(proxy_box, wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(panel, label="URL:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.proxy_ctrl = wx.TextCtrl(panel, value=proxy or "", size=(340, -1))
        self.proxy_ctrl.SetFont(mono_font(10))
        row.Add(self.proxy_ctrl, 1, wx.EXPAND)
        proxy_sizer.Add(row, 0, wx.EXPAND | wx.ALL, 8)
        hint = wx.StaticText(
            panel,
            label="e.g. socks5://127.0.0.1:1080 or http://127.0.0.1:8080",
        )
        hint.SetFont(ui_font(9))
        proxy_sizer.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        sizer.Add(proxy_sizer, 1, wx.EXPAND | wx.ALL, 12)

        if config_path:
            save_hint = wx.StaticText(panel, label=f"Save to: {config_path}")
        else:
            save_hint = wx.StaticText(
                panel,
                label="No config file loaded; Save applies to this session until you choose a save path.",
            )
        save_hint.SetFont(ui_font(9))
        sizer.Add(save_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        buttons = wx.StdDialogButtonSizer()
        btn_ok = wx.Button(panel, wx.ID_OK, "OK")
        btn_cancel = wx.Button(panel, wx.ID_CANCEL, "Cancel")
        btn_save = wx.Button(panel, wx.ID_APPLY, "Save")
        for btn in (btn_ok, btn_cancel, btn_save):
            btn.SetFont(ui_font(10))
        buttons.AddButton(btn_ok)
        buttons.AddButton(btn_save)
        buttons.AddButton(btn_cancel)
        buttons.Realize()
        sizer.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, 12)

        panel.SetSizer(sizer)
        btn_save.Bind(wx.EVT_BUTTON, self._on_save)

    def _on_save(self, _evt) -> None:
        parent = self.GetParent()
        proxy = self.proxy_ctrl.GetValue().strip() or None
        # Duck-type AddressEditFrame to avoid circular import with frame.py.
        if hasattr(parent, "apply_proxy") and hasattr(parent, "config_path"):
            if not parent.config_path:
                dialog = wx.FileDialog(
                    parent,
                    "Save Config",
                    message="Choose config file to save preferences",
                    defaultFile=CONFIG_FILENAME,
                    wildcard="JSON config (*.json)|*.json|All files (*.*)|*.*",
                    style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
                )
                if dialog.ShowModal() != wx.ID_OK:
                    dialog.Destroy()
                    return
                parent.config_path = Path(dialog.GetPath())
                dialog.Destroy()
            parent.apply_proxy(proxy, save_to_config=True)
        self.EndModal(wx.ID_OK)

    def get_proxy(self) -> str | None:
        value = self.proxy_ctrl.GetValue().strip()
        return value or None
