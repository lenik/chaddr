"""Address list panel with CRUD and sortable columns."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from datetime import datetime

import wx

from chaddr.address import (
    AddressEntry,
    AddressSet,
    address_set_from_selection,
    is_ipv4,
    is_ipv6,
)
from chaddr.gui.theme import mono_font, spare_entry_colour, ui_font


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

_COLUMNS = (
    ("Type", 90),
    ("Family", 70),
    ("Address", 170),
    ("Timestamp", 150),
)
_SORT_ASC = " ▲"
_SORT_DESC = " ▼"


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


def _address_sort_key(address: str) -> tuple:
    try:
        ip = ipaddress.ip_address(address.strip())
        return (0 if ip.version == 4 else 1, int(ip))
    except ValueError:
        return (2, address.lower())


def _timestamp_sort_key(timestamp: str) -> tuple:
    text = timestamp.strip()
    if not text:
        return (1, datetime.min)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return (0, datetime.strptime(text, fmt))
        except ValueError:
            continue
    return (2, text)


class AddressListCtrl(wx.ListCtrl):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, style=wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES)
        self._entries: list[AddressEntry] = []
        self._sort_column = 0
        self._sort_ascending = True
        self.SetMinSize((280, 120))
        for index, (label, width) in enumerate(_COLUMNS):
            self.InsertColumn(index, label, width=width)
        self._refresh_column_headers()
        self.Bind(wx.EVT_LIST_COL_CLICK, self._on_column_click)

    def set_entries(self, entries: list[AddressEntry]) -> None:
        self._entries = list(entries)
        self._apply_sort()
        self._rebuild()

    def get_entries(self) -> list[AddressEntry]:
        return list(self._entries)

    def selected_indices(self) -> list[int]:
        indices: list[int] = []
        item = self.GetFirstSelected()
        while item != -1:
            indices.append(item)
            item = self.GetNextSelected(item)
        return indices

    def select_indices(self, indices: list[int]) -> None:
        for index in range(self.GetItemCount()):
            self.Select(index, on=False)
        for index in indices:
            if 0 <= index < self.GetItemCount():
                self.Select(index, on=True)
                self.EnsureVisible(index)

    def _entry_sort_key(self, entry: AddressEntry) -> tuple:
        if self._sort_column == 0:
            return (entry.source.lower(),)
        if self._sort_column == 1:
            # Family / IP version: IPv4 before IPv6, then numeric address.
            version = 4 if entry.family == "IPv4" else 6 if entry.family == "IPv6" else 9
            return (version, _address_sort_key(entry.address))
        if self._sort_column == 2:
            return _address_sort_key(entry.address)
        if self._sort_column == 3:
            return _timestamp_sort_key(entry.timestamp)
        return (entry.source.lower(),)

    def _apply_sort(self) -> None:
        reverse = not self._sort_ascending
        self._entries.sort(key=self._entry_sort_key, reverse=reverse)

    def _refresh_column_headers(self) -> None:
        for index, (label, _width) in enumerate(_COLUMNS):
            item = self.GetColumn(index)
            if index == self._sort_column:
                marker = _SORT_ASC if self._sort_ascending else _SORT_DESC
                item.SetText(label + marker)
            else:
                item.SetText(label)
            self.SetColumn(index, item)

    def _rebuild(self) -> None:
        resolved = {entry.address for entry in self._entries if entry.source == "resolve"}
        gray = spare_entry_colour()
        self.Freeze()
        try:
            self.DeleteAllItems()
            for index, entry in enumerate(self._entries):
                self.InsertItem(index, entry.source)
                self.SetItem(index, 1, entry.family)
                self.SetItem(index, 2, entry.address)
                self.SetItem(index, 3, entry.timestamp)
                if entry.source == "history" and entry.address not in resolved:
                    self.SetItemTextColour(index, gray)
                else:
                    self.SetItemTextColour(index, wx.NullColour)
            if self._entries:
                self.Select(0, on=True)
        finally:
            self.Thaw()
        self._refresh_column_headers()

    def _on_column_click(self, evt: wx.ListEvent) -> None:
        column = evt.GetColumn()
        if column < 0:
            return
        if column == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            self._sort_ascending = True
        selected_addresses = {
            (self._entries[i].family, self._entries[i].address)
            for i in self.selected_indices()
            if 0 <= i < len(self._entries)
        }
        self._apply_sort()
        self._rebuild()
        if selected_addresses:
            indices = [
                index
                for index, entry in enumerate(self._entries)
                if (entry.family, entry.address) in selected_addresses
            ]
            self.select_indices(indices)


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
        family_row.Add(wx.StaticText(panel, label="Family:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
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
        source_row.Add(wx.StaticText(panel, label="Type:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
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
        self.listbox = AddressListCtrl(self)
        self.listbox.SetFont(mono_font(10))
        sizer.Add(self.listbox, 1, wx.EXPAND)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_height = _action_button_height(self)
        self.add_btn = _icon_button(self, wx.ART_PLUS, "Add", btn_height)
        self.edit_btn = _icon_button(self, wx.ART_EDIT, "Edit", btn_height)
        self.delete_btn = _icon_button(self, wx.ART_DELETE, "Delete", btn_height)
        for btn in (self.add_btn, self.edit_btn, self.delete_btn):
            btn_row.Add(btn, 0, _btn_row_flags(), BTN_ROW_BORDER)

        sizer.Add(btn_row, 0, wx.EXPAND | wx.TOP, 4)
        self.SetMinSize((_button_row_min_width(3), -1))
        self.SetSizer(sizer)

        self.add_btn.Bind(wx.EVT_BUTTON, self._on_add)
        self.edit_btn.Bind(wx.EVT_BUTTON, self._on_edit)
        self.delete_btn.Bind(wx.EVT_BUTTON, self._on_delete)
        self.listbox.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_selection_changed)
        self.listbox.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._on_selection_changed)

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
        self._entries = self.listbox.get_entries()
        self._emit_changed()

    def get_entries(self) -> list[AddressEntry]:
        return self.listbox.get_entries()

    def get_selected_entries(self) -> list[AddressEntry]:
        entries = self._entries
        return [entries[i] for i in self.listbox.selected_indices() if 0 <= i < len(entries)]

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
        selected_keys = {
            (self._entries[i].family, self._entries[i].address)
            for i in selected
            if 0 <= i < len(self._entries)
        }
        self.listbox.set_entries(self._entries)
        self._entries = self.listbox.get_entries()
        if selected_keys:
            indices = [
                index
                for index, entry in enumerate(self._entries)
                if (entry.family, entry.address) in selected_keys
            ]
            self.listbox.select_indices(indices)

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
            entry = AddressEntry(
                current.family,
                current.address,
                current.source,
                detail=current.detail,
                timestamp=current.timestamp,
            )
        else:
            entry = AddressEntry(
                entry.family,
                entry.address,
                entry.source,
                detail=current.detail,
                timestamp=current.timestamp,
            )
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
