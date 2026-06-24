"""Side-by-side address list panels with CRUD."""

from __future__ import annotations

import html

import wx
import wx.html

from chaddr.address import (
    AddressEntry,
    AddressSet,
    address_set_from_entries,
    is_ipv4,
    is_ipv6,
)
from chaddr.gui.theme import mono_font, spare_entry_css_colour, ui_font


def _action_button_height(parent: wx.Window) -> int:
    probe = wx.Button(parent, label="Renew")
    probe.SetFont(ui_font(10))
    height = probe.GetBestSize().GetHeight()
    probe.Destroy()
    return max(height, 28)


def _scaled_art_bitmap(art_id: str, size: int) -> wx.Bitmap:
    bitmap = wx.ArtProvider.GetBitmap(art_id, wx.ART_BUTTON, (size, size))
    if bitmap.IsOk() and bitmap.GetSize() == wx.Size(size, size):
        return bitmap
    image = bitmap.ConvertToImage()
    if not image.IsOk():
        return bitmap
    scaled = image.Scale(size, size, wx.IMAGE_QUALITY_HIGH)
    return wx.Bitmap(scaled)


GTK_BUTTON_MARGIN = 14  # GTK ~7px padding per side for bitmap buttons


def _btn_row_flags() -> int:
    """Sizer flags that honour button minimum size (wx version portable)."""
    if hasattr(wx, "FIX_MINSIZE"):
        return wx.ALL | wx.ALIGN_CENTER_VERTICAL | wx.FIX_MINSIZE
    return wx.ALL | wx.ALIGN_CENTER_VERTICAL | wx.ADJUST_MINSIZE


def _icon_button(parent: wx.Window, art_id: str, tooltip: str, height: int) -> wx.Button:
    side = max(height, 36)
    icon_size = max(16, side - GTK_BUTTON_MARGIN)
    bitmap = _scaled_art_bitmap(art_id, icon_size)
    btn = wx.BitmapButton(parent, wx.ID_ANY, bitmap)
    btn.SetMinSize((side, side))
    btn.SetToolTip(tooltip)
    return btn


def _icon_action_button(parent: wx.Window, art_id: str, label: str, height: int) -> wx.Button:
    icon_size = max(16, height - GTK_BUTTON_MARGIN)
    btn = wx.Button(parent, label=label)
    btn.SetFont(ui_font(10))
    btn.SetBitmap(_scaled_art_bitmap(art_id, icon_size))
    best = btn.GetBestSize()
    btn.SetMinSize((max(best.GetWidth(), icon_size + GTK_BUTTON_MARGIN + 48), max(height, 32)))
    btn.SetToolTip(label)
    return btn


class AddressHtmlListBox(wx.html.HtmlListBox):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self._entries: list[AddressEntry] = []
        self.SetMinSize((-1, 100))

    def set_entries(self, entries: list[AddressEntry]) -> None:
        self._entries = list(entries)
        self.SetItemCount(len(self._entries))
        if self._entries:
            self.RefreshAll()

    def get_entries(self) -> list[AddressEntry]:
        return list(self._entries)

    def selected_index(self) -> int:
        return self.GetSelection()

    def select_index(self, index: int) -> None:
        if 0 <= index < len(self._entries):
            self.SetSelection(index)

    def OnGetItem(self, index: int) -> str:
        entry = self._entries[index]
        text = html.escape(entry.display())
        if entry.spare:
            colour = spare_entry_css_colour()
            return f'<span style="color:{colour};font-style:italic">{text}</span>'
        return text


class AddressEntryDialog(wx.Dialog):
    def __init__(
        self,
        parent: wx.Window,
        title: str,
        entry: AddressEntry | None = None,
        *,
        default_source: str = "manual",
        single_family: bool = False,
    ) -> None:
        super().__init__(parent, title=title, size=(420, 220))
        self._single_family = single_family
        panel = wx.Panel(self)
        panel.SetFont(ui_font(10))
        sizer = wx.BoxSizer(wx.VERTICAL)

        family_row = wx.BoxSizer(wx.HORIZONTAL)
        family_row.Add(wx.StaticText(panel, label="Type:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.family_choice = wx.Choice(panel, choices=["IPv4", "IPv6"])
        self.family_choice.SetSelection(0)
        family_row.Add(self.family_choice, 1, wx.EXPAND)
        sizer.Add(family_row, 0, wx.EXPAND | wx.ALL, 10)

        addr_row = wx.BoxSizer(wx.HORIZONTAL)
        addr_row.Add(wx.StaticText(panel, label="Address:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.address_ctrl = wx.TextCtrl(panel)
        self.address_ctrl.SetFont(mono_font(10))
        addr_row.Add(self.address_ctrl, 1, wx.EXPAND)
        sizer.Add(addr_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        source_row = wx.BoxSizer(wx.HORIZONTAL)
        source_row.Add(wx.StaticText(panel, label="Source:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.source_ctrl = wx.TextCtrl(panel, value=default_source)
        self.source_ctrl.SetFont(ui_font(10))
        source_row.Add(self.source_ctrl, 1, wx.EXPAND)
        sizer.Add(source_row, 0, wx.EXPAND | wx.ALL, 10)

        buttons = wx.StdDialogButtonSizer()
        btn_ok = wx.Button(panel, wx.ID_OK, "OK")
        btn_cancel = wx.Button(panel, wx.ID_CANCEL, "Cancel")
        buttons.AddButton(btn_ok)
        buttons.AddButton(btn_cancel)
        buttons.Realize()
        sizer.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(sizer)

        if entry is not None:
            self.family_choice.SetStringSelection(entry.family)
            self.address_ctrl.SetValue(entry.address)
            self.source_ctrl.SetValue(entry.source)

        if single_family and entry is not None:
            self.family_choice.Disable()

    def get_entry(self) -> AddressEntry:
        family = self.family_choice.GetStringSelection()
        address = self.address_ctrl.GetValue().strip()
        source = self.source_ctrl.GetValue().strip() or "manual"
        if family == "IPv4" and not is_ipv4(address):
            raise ValueError(f"invalid IPv4: {address}")
        if family == "IPv6" and not is_ipv6(address):
            raise ValueError(f"invalid IPv6: {address}")
        return AddressEntry(family, address, source)


class AddressListPanel(wx.Panel):
    def __init__(
        self,
        parent: wx.Window,
        *,
        default_source: str = "manual",
        limit_one_per_family: bool = False,
        trailing: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._default_source = default_source
        self._limit_one_per_family = limit_one_per_family
        self._entries: list[AddressEntry] = []
        self._trailing = trailing
        self._trailing_buttons: list[wx.Button] = []
        self.SetFont(ui_font(10))

        sizer = wx.BoxSizer(wx.VERTICAL)
        self.listbox = AddressHtmlListBox(self)
        self.listbox.SetFont(mono_font(10))
        sizer.Add(self.listbox, 1, wx.EXPAND)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_height = _action_button_height(self)
        self.add_btn = _icon_button(self, wx.ART_PLUS, "Add", btn_height)
        self.edit_btn = _icon_button(self, wx.ART_EDIT, "Edit", btn_height)
        self.delete_btn = _icon_button(self, wx.ART_DELETE, "Delete", btn_height)
        for btn in (self.add_btn, self.edit_btn, self.delete_btn):
            btn_row.Add(btn, 0, _btn_row_flags(), 2)

        if trailing == "diagnose":
            btn_row.AddStretchSpacer(1)
            self.diagnose_btn = _icon_action_button(self, wx.ART_FIND, "Diagnose", btn_height)
            self._trailing_buttons.append(self.diagnose_btn)
            btn_row.Add(self.diagnose_btn, 0, _btn_row_flags(), 2)
        elif trailing == "renew_apply":
            self.renew_btn = _icon_action_button(self, wx.ART_REDO, "Renew", btn_height)
            self.apply_btn = _icon_action_button(self, wx.ART_TICK_MARK, "Apply", btn_height)
            for btn in (self.renew_btn, self.apply_btn):
                self._trailing_buttons.append(btn)
            btn_row.Add(self.renew_btn, 0, _btn_row_flags(), 2)
            btn_row.AddStretchSpacer(1)
            btn_row.Add(self.apply_btn, 0, _btn_row_flags(), 2)

        sizer.Add(btn_row, 0, wx.EXPAND | wx.TOP, 4)
        self.SetSizer(sizer)

        self.add_btn.Bind(wx.EVT_BUTTON, self._on_add)
        self.edit_btn.Bind(wx.EVT_BUTTON, self._on_edit)
        self.delete_btn.Bind(wx.EVT_BUTTON, self._on_delete)

    def trailing_buttons(self) -> list[wx.Button]:
        return list(self._trailing_buttons)

    def set_entries(self, entries: list[AddressEntry]) -> None:
        self._entries = list(entries)
        self.listbox.set_entries(self._entries)

    def get_entries(self) -> list[AddressEntry]:
        return self.listbox.get_entries()

    def get_address_set(self) -> AddressSet:
        return address_set_from_entries(self._entries)

    def set_address_set(self, addresses: AddressSet, source: str) -> None:
        self.set_entries(AddressEntry.from_address_set(addresses, source))

    def set_enabled(self, enabled: bool) -> None:
        self.listbox.Enable(enabled)
        self.add_btn.Enable(enabled)
        self.edit_btn.Enable(enabled)
        self.delete_btn.Enable(enabled)

    def set_trailing_enabled(self, enabled: bool) -> None:
        for btn in self._trailing_buttons:
            btn.Enable(enabled)

    def _refresh_list(self) -> None:
        self.listbox.set_entries(self._entries)

    def _selected_index(self) -> int:
        return self.listbox.selected_index()

    def _on_add(self, _evt) -> None:
        dialog = AddressEntryDialog(
            self,
            "Add address",
            default_source=self._default_source,
        )
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        try:
            entry = dialog.get_entry()
        except ValueError as exc:
            dialog.Destroy()
            wx.MessageBox(str(exc), "Invalid address", wx.OK | wx.ICON_WARNING)
            return
        dialog.Destroy()
        if self._limit_one_per_family and any(item.family == entry.family for item in self._entries):
            wx.MessageBox(
                f"A {entry.family} entry already exists; edit or delete it before adding another.",
                "Duplicate family",
                wx.OK | wx.ICON_WARNING,
            )
            return
        self._entries.append(entry)
        self._refresh_list()
        self.listbox.select_index(len(self._entries) - 1)

    def _on_edit(self, _evt) -> None:
        index = self._selected_index()
        if index == wx.NOT_FOUND:
            wx.MessageBox("Select an address first.", "No selection", wx.OK | wx.ICON_INFORMATION)
            return
        current = self._entries[index]
        dialog = AddressEntryDialog(
            self,
            "Edit address",
            entry=current,
            default_source=self._default_source,
            single_family=self._limit_one_per_family,
        )
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        try:
            entry = dialog.get_entry()
        except ValueError as exc:
            dialog.Destroy()
            wx.MessageBox(str(exc), "Invalid address", wx.OK | wx.ICON_WARNING)
            return
        dialog.Destroy()
        if self._limit_one_per_family and entry.family != current.family:
            wx.MessageBox("The address family cannot be changed.", "Invalid edit", wx.OK | wx.ICON_WARNING)
            return
        if self._limit_one_per_family:
            for idx, item in enumerate(self._entries):
                if idx != index and item.family == entry.family:
                    wx.MessageBox(
                        f"A {entry.family} entry already exists.",
                        "Duplicate family",
                        wx.OK | wx.ICON_WARNING,
                    )
                    return
        entry.spare = current.spare
        self._entries[index] = entry
        self._refresh_list()
        self.listbox.select_index(index)

    def _on_delete(self, _evt) -> None:
        index = self._selected_index()
        if index == wx.NOT_FOUND:
            wx.MessageBox("Select an address first.", "No selection", wx.OK | wx.ICON_INFORMATION)
            return
        del self._entries[index]
        self._refresh_list()
        if self._entries:
            self.listbox.select_index(min(index, len(self._entries) - 1))
