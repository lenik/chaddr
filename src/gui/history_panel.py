"""Profile history editor panel (history-addr records)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import wx

from chaddr.address import is_ipv4, is_ipv6
from chaddr.gui.address_panel import (
    BTN_ROW_BORDER,
    _action_button_height,
    _address_sort_key,
    _btn_row_flags,
    _button_row_min_width,
    _icon_button,
    _timestamp_sort_key,
)
from chaddr.gui.theme import mono_font, ui_font
from chaddr.profile import AddrHistoryRecord, format_addr_history_timestamp

_COLUMNS = (
    ("Address", 160),
    ("Insert time", 150),
    ("Event time", 150),
)
_SORT_ASC = " ▲"
_SORT_DESC = " ▼"


class HistoryEntryDialog(wx.Dialog):
    def __init__(
        self,
        parent: wx.Window,
        title: str,
        record: AddrHistoryRecord | None = None,
    ) -> None:
        super().__init__(parent, title=title, size=(460, 240))
        panel = wx.Panel(self)
        panel.SetFont(ui_font(10))
        sizer = wx.BoxSizer(wx.VERTICAL)

        addr_row = wx.BoxSizer(wx.HORIZONTAL)
        addr_row.Add(wx.StaticText(panel, label="Address:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.address_ctrl = wx.TextCtrl(panel)
        self.address_ctrl.SetFont(mono_font(10))
        addr_row.Add(self.address_ctrl, 1, wx.EXPAND)
        sizer.Add(addr_row, 0, wx.EXPAND | wx.ALL, 10)

        insert_row = wx.BoxSizer(wx.HORIZONTAL)
        insert_row.Add(
            wx.StaticText(panel, label="Insert time:"),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            8,
        )
        self.insert_ctrl = wx.TextCtrl(panel)
        self.insert_ctrl.SetFont(mono_font(10))
        self.insert_ctrl.SetHint("YYYY-MM-DD HH:MM:SS")
        insert_row.Add(self.insert_ctrl, 1, wx.EXPAND)
        sizer.Add(insert_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        event_row = wx.BoxSizer(wx.HORIZONTAL)
        event_row.Add(
            wx.StaticText(panel, label="Event time:"),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            8,
        )
        self.event_ctrl = wx.TextCtrl(panel)
        self.event_ctrl.SetFont(mono_font(10))
        self.event_ctrl.SetHint("optional")
        event_row.Add(self.event_ctrl, 1, wx.EXPAND)
        sizer.Add(event_row, 0, wx.EXPAND | wx.ALL, 10)

        if record is not None:
            self.address_ctrl.SetValue(record.address)
            self.insert_ctrl.SetValue(record.insert_time)
            self.event_ctrl.SetValue(record.event_time)
        else:
            self.insert_ctrl.SetValue(format_addr_history_timestamp())

        buttons = wx.StdDialogButtonSizer()
        buttons.AddButton(wx.Button(panel, wx.ID_OK, "OK"))
        buttons.AddButton(wx.Button(panel, wx.ID_CANCEL, "Cancel"))
        buttons.Realize()
        sizer.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        panel.SetSizer(sizer)

    def get_record(self) -> AddrHistoryRecord:
        address = self.address_ctrl.GetValue().strip()
        if not (is_ipv4(address) or is_ipv6(address)):
            raise ValueError(f"invalid address: {address}")
        insert_time = self.insert_ctrl.GetValue().strip()
        event_time = self.event_ctrl.GetValue().strip()
        for label, value in (("Insert time", insert_time), ("Event time", event_time)):
            if not value:
                continue
            ok = False
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    datetime.strptime(value, fmt)
                    ok = True
                    break
                except ValueError:
                    continue
            if not ok:
                raise ValueError(f"{label} must be YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")
        return AddrHistoryRecord(address, insert_time=insert_time, event_time=event_time)


class HistoryListCtrl(wx.ListCtrl):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, style=wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES)
        self._records: list[AddrHistoryRecord] = []
        self._sort_column = 1
        self._sort_ascending = False
        self.SetMinSize((280, 120))
        for index, (label, width) in enumerate(_COLUMNS):
            self.InsertColumn(index, label, width=width)
        self._refresh_column_headers()
        self.Bind(wx.EVT_LIST_COL_CLICK, self._on_column_click)

    def set_records(self, records: list[AddrHistoryRecord]) -> None:
        self._records = list(records)
        self._apply_sort()
        self._rebuild()

    def get_records(self) -> list[AddrHistoryRecord]:
        return list(self._records)

    def selected_indices(self) -> list[int]:
        indices: list[int] = []
        item = self.GetFirstSelected()
        while item != -1:
            indices.append(item)
            item = self.GetNextSelected(item)
        return indices

    def select_indices(self, indices: list[int]) -> None:
        wanted = set(indices)
        for index in range(self.GetItemCount()):
            self.Select(index, on=index in wanted)

    def _on_column_click(self, evt) -> None:
        column = evt.GetColumn()
        if column == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            self._sort_ascending = column != 1
        self._apply_sort()
        self._rebuild()
        self._refresh_column_headers()

    def _sort_key(self, record: AddrHistoryRecord):
        if self._sort_column == 0:
            return _address_sort_key(record.address)
        if self._sort_column == 1:
            return _timestamp_sort_key(record.insert_time)
        return _timestamp_sort_key(record.event_time)

    def _apply_sort(self) -> None:
        self._records.sort(key=self._sort_key, reverse=not self._sort_ascending)

    def _refresh_column_headers(self) -> None:
        for index, (label, _) in enumerate(_COLUMNS):
            marker = ""
            if index == self._sort_column:
                marker = _SORT_ASC if self._sort_ascending else _SORT_DESC
            self.SetColumnWidth(index, self.GetColumnWidth(index))
            item = self.GetColumn(index)
            item.SetText(f"{label}{marker}")
            self.SetColumn(index, item)

    def _rebuild(self) -> None:
        self.DeleteAllItems()
        for record in self._records:
            index = self.InsertItem(self.GetItemCount(), record.address)
            self.SetItem(index, 1, record.insert_time)
            self.SetItem(index, 2, record.event_time)


class HistoryListPanel(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self.SetFont(ui_font(10))
        self._records: list[AddrHistoryRecord] = []
        self._dirty = False
        self._on_changed: Callable[[], None] | None = None
        self._on_save: Callable[[list[AddrHistoryRecord]], bool] | None = None

        root = wx.BoxSizer(wx.VERTICAL)
        self.listbox = HistoryListCtrl(self)
        root.Add(self.listbox, 1, wx.EXPAND | wx.ALL, 4)

        btn_height = _action_button_height(self)
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.SetMinSize((_button_row_min_width(4), -1))
        self.add_btn = _icon_button(self, wx.ART_PLUS, "Add", btn_height)
        self.edit_btn = _icon_button(self, wx.ART_REPORT_VIEW, "Edit", btn_height)
        self.delete_btn = _icon_button(self, wx.ART_MINUS, "Delete", btn_height)
        self.save_btn = _icon_button(self, wx.ART_FILE_SAVE, "Save history", btn_height)
        for btn in (self.add_btn, self.edit_btn, self.delete_btn, self.save_btn):
            btn_row.Add(btn, 0, _btn_row_flags(), BTN_ROW_BORDER)
        btn_row.AddStretchSpacer(1)
        root.Add(btn_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)
        self.SetSizer(root)

        self.add_btn.Bind(wx.EVT_BUTTON, self._on_add)
        self.edit_btn.Bind(wx.EVT_BUTTON, self._on_edit)
        self.delete_btn.Bind(wx.EVT_BUTTON, self._on_delete)
        self.save_btn.Bind(wx.EVT_BUTTON, self._on_save_clicked)
        self.listbox.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_edit)
        self._refresh_save_enabled()

    def set_on_changed(self, callback: Callable[[], None] | None) -> None:
        self._on_changed = callback

    def set_on_save(self, callback: Callable[[list[AddrHistoryRecord]], bool] | None) -> None:
        self._on_save = callback

    def set_records(self, records: list[AddrHistoryRecord], *, dirty: bool = False) -> None:
        self._records = list(records)
        self._dirty = dirty
        self.listbox.set_records(self._records)
        self._records = self.listbox.get_records()
        self._refresh_save_enabled()
        self._emit_changed()

    def get_records(self) -> list[AddrHistoryRecord]:
        return self.listbox.get_records()

    def is_dirty(self) -> bool:
        return self._dirty

    def _emit_changed(self) -> None:
        if self._on_changed is not None:
            self._on_changed()

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._refresh_save_enabled()
        self._emit_changed()

    def _refresh_save_enabled(self) -> None:
        self.save_btn.Enable(self._dirty and self._on_save is not None)

    def _sync_from_list(self) -> None:
        self._records = self.listbox.get_records()

    def _on_add(self, _evt) -> None:
        dialog = HistoryEntryDialog(self, "Add history address")
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        try:
            record = dialog.get_record()
        except ValueError as exc:
            dialog.Destroy()
            wx.MessageBox(str(exc), "Invalid history entry", wx.OK | wx.ICON_WARNING)
            return
        dialog.Destroy()
        records = self.get_records()
        if any(item.address == record.address for item in records):
            wx.MessageBox(
                f"Address {record.address} is already in history.",
                "Duplicate",
                wx.OK | wx.ICON_INFORMATION,
            )
            return
        records.append(record)
        self.set_records(records, dirty=True)

    def _on_edit(self, _evt) -> None:
        indices = self.listbox.selected_indices()
        if len(indices) != 1:
            wx.MessageBox("Select one history entry to edit.", "Edit", wx.OK | wx.ICON_INFORMATION)
            return
        index = indices[0]
        records = self.get_records()
        dialog = HistoryEntryDialog(self, "Edit history address", records[index])
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        try:
            record = dialog.get_record()
        except ValueError as exc:
            dialog.Destroy()
            wx.MessageBox(str(exc), "Invalid history entry", wx.OK | wx.ICON_WARNING)
            return
        dialog.Destroy()
        if any(i != index and item.address == record.address for i, item in enumerate(records)):
            wx.MessageBox(
                f"Address {record.address} is already in history.",
                "Duplicate",
                wx.OK | wx.ICON_INFORMATION,
            )
            return
        records[index] = record
        self.set_records(records, dirty=True)
        self.listbox.select_indices([index])

    def _on_delete(self, _evt) -> None:
        indices = sorted(self.listbox.selected_indices(), reverse=True)
        if not indices:
            return
        records = self.get_records()
        for index in indices:
            del records[index]
        self.set_records(records, dirty=True)

    def _on_save_clicked(self, _evt) -> None:
        if self._on_save is None:
            return
        records = self.get_records()
        if self._on_save(records):
            self._dirty = False
            self._refresh_save_enabled()
