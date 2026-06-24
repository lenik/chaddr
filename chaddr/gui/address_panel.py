"""Address list panel with CRUD and profile actions."""

from __future__ import annotations

from collections.abc import Callable

import wx

from chaddr.address import (
    AddressEntry,
    AddressSet,
    address_set_from_selection,
    is_ipv4,
    is_ipv6,
)
from chaddr.gui.theme import mono_font, ui_font


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


GTK_BUTTON_MARGIN = 14
ICON_BUTTON_SIDE = 32
BTN_ROW_BORDER = 2


def _btn_row_flags() -> int:
    if hasattr(wx, "FIX_MINSIZE"):
        return wx.ALIGN_CENTER_VERTICAL | wx.FIX_MINSIZE
    return wx.ALIGN_CENTER_VERTICAL | wx.ADJUST_MINSIZE


def _button_row_min_width(button_count: int, side: int = ICON_BUTTON_SIDE) -> int:
    per_button = side + BTN_ROW_BORDER * 2
    return button_count * per_button + BTN_ROW_BORDER * 2


def _icon_button(parent: wx.Window, art_id: str, tooltip: str, height: int) -> wx.Button:
    side = max(height, ICON_BUTTON_SIDE)
    icon_size = max(16, side - GTK_BUTTON_MARGIN)
    bitmap = _scaled_art_bitmap(art_id, icon_size)
    btn = wx.BitmapButton(parent, wx.ID_ANY, bitmap, size=(side, side))
    btn.SetMinSize((side, side))
    btn.SetMaxSize((side, side))
    btn.SetToolTip(tooltip)
    return btn


class AddressListBox(wx.ListBox):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, style=wx.LB_EXTENDED)
        self._entries: list[AddressEntry] = []
        self.SetMinSize((280, 120))

    def set_entries(self, entries: list[AddressEntry]) -> None:
        self._entries = list(entries)
        self.Freeze()
        try:
            self.Clear()
            for entry in self._entries:
                self.Append(entry.display())
            if self._entries:
                self.SetSelection(0)
        finally:
            self.Thaw()

    def get_entries(self) -> list[AddressEntry]:
        return list(self._entries)

    def selected_indices(self) -> list[int]:
        return list(self.GetSelections())

    def select_indices(self, indices: list[int]) -> None:
        self.SetSelection(-1)
        for index in indices:
            if 0 <= index < len(self._entries):
                self.SetSelection(index)


class AddressEntryDialog(wx.Dialog):
    def __init__(
        self,
        parent: wx.Window,
        title: str,
        entry: AddressEntry | None = None,
        *,
        editable_source: bool = True,
    ) -> None:
        super().__init__(parent, title=title, size=(420, 220))
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
        self.source_ctrl = wx.TextCtrl(panel, value="manual")
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

        if not editable_source:
            self.source_ctrl.Disable()
        if entry is not None and not editable_source:
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
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self._entries: list[AddressEntry] = []
        self._on_changed: Callable[[], None] | None = None
        self.SetFont(ui_font(10))

        sizer = wx.BoxSizer(wx.VERTICAL)
        self.listbox = AddressListBox(self)
        self.listbox.SetFont(mono_font(10))
        sizer.Add(self.listbox, 1, wx.EXPAND)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_height = _action_button_height(self)
        self.add_btn = _icon_button(self, wx.ART_PLUS, "Add", btn_height)
        self.edit_btn = _icon_button(self, wx.ART_EDIT, "Edit", btn_height)
        self.delete_btn = _icon_button(self, wx.ART_DELETE, "Delete", btn_height)
        for btn in (self.add_btn, self.edit_btn, self.delete_btn):
            btn_row.Add(btn, 0, _btn_row_flags(), BTN_ROW_BORDER)

        btn_row.AddStretchSpacer(1)
        self.diagnose_btn = _icon_button(self, wx.ART_FIND, "Diagnose", btn_height)
        self.renew_btn = _icon_button(self, wx.ART_REDO, "Renew", btn_height)
        self.apply_btn = _icon_button(self, wx.ART_TICK_MARK, "Apply", btn_height)
        for btn in (self.diagnose_btn, self.renew_btn, self.apply_btn):
            btn_row.Add(btn, 0, _btn_row_flags(), BTN_ROW_BORDER)

        sizer.Add(btn_row, 0, wx.EXPAND | wx.TOP, 4)
        self.SetMinSize((_button_row_min_width(6), -1))
        self.SetSizer(sizer)

        self.add_btn.Bind(wx.EVT_BUTTON, self._on_add)
        self.edit_btn.Bind(wx.EVT_BUTTON, self._on_edit)
        self.delete_btn.Bind(wx.EVT_BUTTON, self._on_delete)
        self.listbox.Bind(wx.EVT_LISTBOX, self._on_selection_changed)

    def set_on_changed(self, callback: Callable[[], None] | None) -> None:
        self._on_changed = callback

    def _emit_changed(self) -> None:
        if self._on_changed is not None:
            self._on_changed()

    def merge_entries(
        self,
        incoming: list[AddressEntry],
        *,
        replace_sources: frozenset[str] | None = None,
    ) -> None:
        from chaddr.address import merge_address_entries

        self._entries = merge_address_entries(
            self._entries,
            incoming,
            replace_sources=replace_sources,
            distinct_sources=True,
        )
        self._refresh_list()

    def set_entries(self, entries: list[AddressEntry]) -> None:
        self._entries = list(entries)
        self.listbox.set_entries(self._entries)
        self._emit_changed()

    def get_entries(self) -> list[AddressEntry]:
        return self.listbox.get_entries()

    def get_selected_entries(self) -> list[AddressEntry]:
        return [self._entries[i] for i in self.listbox.selected_indices() if 0 <= i < len(self._entries)]

    def get_apply_address_set(self) -> AddressSet:
        return address_set_from_selection(self._entries, self.listbox.selected_indices())

    def has_valid_apply_selection(self) -> bool:
        try:
            address_set_from_selection(self._entries, self.listbox.selected_indices())
        except ValueError:
            return False
        return True

    def _refresh_list(self, *, preserve_selection: bool = True) -> None:
        selected = self.listbox.selected_indices() if preserve_selection else []
        self.listbox.set_entries(self._entries)
        if selected:
            self.listbox.select_indices(selected)

    def _on_selection_changed(self, _evt) -> None:
        self._emit_changed()

    def _on_add(self, _evt) -> None:
        dialog = AddressEntryDialog(self, "Add address", editable_source=False)
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        try:
            entry = dialog.get_entry()
            entry.source = "manual"
        except ValueError as exc:
            dialog.Destroy()
            wx.MessageBox(str(exc), "Invalid address", wx.OK | wx.ICON_WARNING)
            return
        dialog.Destroy()
        if any(item.family == entry.family and item.address == entry.address for item in self._entries):
            wx.MessageBox("That address is already in the list.", "Duplicate", wx.OK | wx.ICON_WARNING)
            return
        self._entries.append(entry)
        self._refresh_list(preserve_selection=False)
        self.listbox.select_indices([len(self._entries) - 1])
        self._emit_changed()

    def _on_edit(self, _evt) -> None:
        indices = self.listbox.selected_indices()
        if len(indices) != 1:
            wx.MessageBox("Select exactly one address to edit.", "Selection", wx.OK | wx.ICON_INFORMATION)
            return
        index = indices[0]
        current = self._entries[index]
        editable = current.source == "manual"
        dialog = AddressEntryDialog(
            self,
            "Edit address",
            entry=current,
            editable_source=editable,
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
        if not editable:
            entry = AddressEntry(current.family, current.address, current.source)
        if any(
            idx != index and item.family == entry.family and item.address == entry.address
            for idx, item in enumerate(self._entries)
        ):
            wx.MessageBox("That address is already in the list.", "Duplicate", wx.OK | wx.ICON_WARNING)
            return
        self._entries[index] = entry
        self._refresh_list()
        self.listbox.select_indices([index])
        self._emit_changed()

    def _on_delete(self, _evt) -> None:
        indices = sorted(self.listbox.selected_indices(), reverse=True)
        if not indices:
            wx.MessageBox("Select at least one address to delete.", "Selection", wx.OK | wx.ICON_INFORMATION)
            return
        for index in indices:
            if 0 <= index < len(self._entries):
                del self._entries[index]
        self._refresh_list(preserve_selection=False)
        if self._entries:
            self.listbox.select_indices([min(indices[0], len(self._entries) - 1)])
        self._emit_changed()
