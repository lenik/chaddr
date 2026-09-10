"""Profile list / selection mixin for AddressEditFrame."""

from __future__ import annotations

from pathlib import Path

import wx

from chaddr.profile import (
    AddrHistoryRecord,
    ensure_profile_dir,
    format_profile_dir_label,
    get_profile_dir,
    list_profile_items,
    list_profile_instructions,
    load_profile,
    set_profile_dir,
    write_profile_history,
)


class ProfileListMixin:
    def _count_resources(self, profiles: list[str]) -> int:
        total = 0
        for name in profiles:
            profile = load_profile(name)
            total += len(profile.entries)
            if profile.from_block is not None:
                total += 1
        return total

    def _profile_index(self, name: str) -> int | None:
        try:
            return self._profile_names.index(name)
        except ValueError:
            return None

    def _set_profile_status(self, name: str, status: str) -> None:
        if status:
            self._profile_status[name] = status
        else:
            self._profile_status.pop(name, None)
        selected = self._selected_profiles()
        if selected and selected[0] == name:
            self.profile_status_label.SetLabel(status)
            self._control_panel.Layout()

    def _selected_profile_indices(self) -> list[int]:
        index = self.profile_choice.GetSelection()
        if index == wx.NOT_FOUND or index < 0:
            return []
        return [index]

    def _clear_profile_selection(self) -> None:
        if self.profile_choice.GetCount():
            self.profile_choice.SetSelection(wx.NOT_FOUND)

    def _select_profile_indices(self, indices: list[int]) -> None:
        if not indices:
            self._clear_profile_selection()
            return
        index = indices[0]
        if 0 <= index < self.profile_choice.GetCount():
            self.profile_choice.SetSelection(index)

    def _update_profile_box_label(self) -> None:
        self.profile_title.SetLabel(format_profile_dir_label())
        profile_sizer = self.profile_box.GetContainingSizer()
        if profile_sizer is not None:
            profile_sizer.Layout()
        self._control_panel.Layout()

    def _load_profile_list(self, initial: list[str], select: set[str] | None = None) -> None:
        ensure_profile_dir()
        self._update_profile_box_label()
        items = list_profile_items()
        self._profile_names = [name for name, _label in items]
        self._profile_labels = [_label for _name, _label in items]
        self.profile_choice.Clear()
        for label in self._profile_labels:
            self.profile_choice.Append(label)
        if select is not None:
            selected = select
        elif initial:
            selected = set(initial)
        else:
            selected = set(self._profile_names[:1]) if self._profile_names else set()
        self._clear_profile_selection()
        for index, name in enumerate(self._profile_names):
            if name in selected:
                self._select_profile_indices([index])
                break
        current = self._selected_profiles()
        self.profile_status_label.SetLabel(
            self._profile_status.get(current[0], "") if current else ""
        )
        self._resource_count = self._count_resources(self._selected_profiles())
        self._refresh_instructions()
        self._refresh_action_buttons()
        self._refresh_addresses_from_profile()
        self._update_status_bar()

    def _selected_profiles(self) -> list[str]:
        return [self._profile_names[i] for i in self._selected_profile_indices()]

    def _refresh_instructions(self) -> None:
        selected = self._selected_profiles()
        if len(selected) != 1:
            self.instructions_panel.set_instructions([])
            return
        try:
            profile = load_profile(selected[0])
            self.instructions_panel.set_instructions(list_profile_instructions(profile))
        except Exception as exc:
            self.logger.warning("Could not load instructions for %s: %s", selected[0], exc)
            self.instructions_panel.set_instructions([])

    def _refresh_history_from_profile(self) -> None:
        selected = self._selected_profiles()
        if len(selected) != 1:
            self.history_panel.set_records([])
            return
        try:
            profile = load_profile(selected[0])
            self.history_panel.set_records(profile.addr_history_records())
        except Exception as exc:
            self.logger.warning("Could not load history for %s: %s", selected[0], exc)
            self.history_panel.set_records([])

    def _save_profile_history(self, records: list[AddrHistoryRecord]) -> bool:
        selected = self._selected_profiles()
        if len(selected) != 1:
            wx.MessageBox("Select a single profile to save history.", "History", wx.OK | wx.ICON_INFORMATION)
            return False
        name = selected[0]
        try:
            profile = load_profile(name)
        except Exception as exc:
            wx.MessageBox(str(exc), "History", wx.OK | wx.ICON_ERROR)
            return False
        if profile.path is None or not profile.path.is_file():
            wx.MessageBox("Profile path is not writable.", "History", wx.OK | wx.ICON_ERROR)
            return False
        try:
            write_profile_history(profile.path, records)
        except OSError as exc:
            wx.MessageBox(str(exc), "History", wx.OK | wx.ICON_ERROR)
            return False
        self.logger.info("Saved %d history-addr entr(ies) to %s", len(records), profile.path)
        self._refresh_addresses_from_profile()
        return True

    def _load_session_profile(self, name: str):
        profile = load_profile(name)
        return profile.with_selected_instructions(self.instructions_panel.selected_keys())

    def _on_profile_selection(self, _evt) -> None:
        selected = self._selected_profiles()
        self.profile_status_label.SetLabel(
            self._profile_status.get(selected[0], "") if selected else ""
        )
        self._resource_count = self._count_resources(selected)
        self._refresh_instructions()
        self._refresh_history_from_profile()
        self._refresh_action_buttons()
        self._refresh_addresses_from_profile()
        self._update_status_bar()

    def _refresh_manual_mode(self) -> None:
        self._refresh_action_buttons()

    def _on_refresh_profiles(self, _evt) -> None:
        selected = set(self._selected_profiles())
        self._load_profile_list([], select=selected)
        self.logger.info("Refreshed profile list from %s", get_profile_dir())

    def _on_load_profile(self, _evt) -> None:
        dialog = wx.FileDialog(
            self,
            "Load Profile",
            defaultDir=str(get_profile_dir()),
            wildcard="Profile files (*)|*|All files (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        path = Path(dialog.GetPath())
        dialog.Destroy()
        set_profile_dir(path.parent)
        self._load_profile_list([], select={path.name})
        self.logger.info("Loaded profile %s from %s", path.name, path.parent)

    def _on_browse_profile_dir(self, _evt) -> None:
        dialog = wx.DirDialog(self, "Browse Profile Directory", defaultPath=str(get_profile_dir()))
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        set_profile_dir(Path(dialog.GetPath()))
        dialog.Destroy()
        self._load_profile_list([])
        self.logger.info("Profile directory: %s", get_profile_dir())
