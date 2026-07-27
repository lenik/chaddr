"""Instructions checklist panel (profile AST selection for the session)."""

from __future__ import annotations

from collections.abc import Callable

import wx

from chaddr.gui.theme import mono_font, ui_font
from chaddr.profile import ProfileInstruction


class InstructionsPanel(wx.Panel):
    """Toggleable list of profile instructions; default is all selected."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self._instructions: list[ProfileInstruction] = []
        self._on_changed: Callable[[], None] | None = None
        self.SetFont(ui_font(10))

        sizer = wx.BoxSizer(wx.VERTICAL)
        self.listbox = wx.CheckListBox(self, style=wx.LB_EXTENDED)
        self.listbox.SetFont(mono_font(10))
        self.listbox.SetMinSize((280, 120))
        sizer.Add(self.listbox, 1, wx.EXPAND)
        self.SetSizer(sizer)

        self.listbox.Bind(wx.EVT_CHECKLISTBOX, self._on_check)
        self.listbox.Bind(wx.EVT_LEFT_DOWN, self._on_left_down)

    def set_on_changed(self, callback: Callable[[], None] | None) -> None:
        self._on_changed = callback

    def set_instructions(self, instructions: list[ProfileInstruction]) -> None:
        self._instructions = list(instructions)
        self.listbox.Freeze()
        try:
            self.listbox.Clear()
            for item in self._instructions:
                index = self.listbox.Append(item.summary)
                self.listbox.Check(index, True)
        finally:
            self.listbox.Thaw()
        self._notify()

    def selected_keys(self) -> set[str]:
        keys: set[str] = set()
        for index, item in enumerate(self._instructions):
            if self.listbox.IsChecked(index):
                keys.add(item.key)
        return keys

    def select_all(self) -> None:
        for index in range(self.listbox.GetCount()):
            self.listbox.Check(index, True)
        self._notify()

    def _notify(self) -> None:
        if self._on_changed:
            self._on_changed()

    def _on_check(self, _evt) -> None:
        self._notify()

    def _on_left_down(self, evt: wx.MouseEvent) -> None:
        """Toggle check on row click (not only the checkbox glyph)."""
        index = self.listbox.HitTest(evt.GetPosition())
        if index != wx.NOT_FOUND:
            self.listbox.Check(index, not self.listbox.IsChecked(index))
            self._notify()
            return
        evt.Skip()
