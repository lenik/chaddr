"""Right output pane, logging tabs, and diagnostics append helpers."""

from __future__ import annotations

import logging

import wx

from chaddr.gui.highlighter import append_lines, clear_text

from .ids import ID_VIEW_RIGHT_PANE
from .log_support import GuiLogHandler, _profile_log_context
from .widgets import _make_text_ctrl


class OutputPaneMixin:
    def _on_toggle_right_pane(self, evt) -> None:
        if evt.IsChecked():
            if not self._main_split.IsSplit():
                self._right_panel.SetMinSize((200, 120))
                self._main_split.SplitVertically(
                    self._control_panel,
                    self._right_panel,
                    sashPosition=self._initial_main_sash(),
                )
                self._main_split_sash_set = True
                wx.CallAfter(self._center_main_splitter)
        elif self._main_split.IsSplit():
            self._main_split.Unsplit(self._right_panel)
            self._right_panel.SetMinSize((1, 1))

    def _show_right_pane(self, *, tab: int | None = None) -> None:
        menu = self.GetMenuBar()
        if not self._main_split.IsSplit():
            menu.Check(ID_VIEW_RIGHT_PANE, True)
            self._right_panel.SetMinSize((200, 120))
            self._main_split.SplitVertically(
                self._control_panel,
                self._right_panel,
                sashPosition=self._initial_main_sash(),
            )
            self._main_split_sash_set = True
            wx.CallAfter(self._center_main_splitter)
        if tab is not None:
            self._notebook.SetSelection(tab)

    def _prepare_output_panes(self, *, diagnostics: bool = False, logging: bool = True) -> None:
        if diagnostics:
            self._show_right_pane(tab=self.TAB_DIAGNOSTICS)
        elif logging:
            self._show_right_pane(tab=self.TAB_LOGGING)

    def _setup_logging(self) -> None:
        handler = GuiLogHandler(self._append_log)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        self.logger.addHandler(handler)

    def _append_log(self, message: str, level: str = "info", profile_name: str | None = None) -> None:
        text = message.rstrip("\n")
        if level == "warning":
            self._warning_count += 1
            self._warning_messages.append(text)
        elif level in ("error", "critical"):
            self._error_count += 1
            self._error_messages.append(text)
        active = profile_name or getattr(_profile_log_context, "name", None)
        if active and active in self._log_ctrls:
            targets = [self._log_ctrls[active]]
        elif self._log_ctrls:
            targets = list(self._log_ctrls.values())
        else:
            # Still refresh status so warn/error counts are visible without a log tab.
            self._update_status_bar()
            return
        for ctrl in targets:
            key = id(ctrl)
            if key not in self._log_buffer:
                self._log_buffer[key] = (ctrl, [])
            self._log_buffer[key][1].append(message)
        if not self._log_flush_pending:
            self._log_flush_pending = True
            wx.CallAfter(self._flush_log_buffer)

    def _flush_log_buffer(self) -> None:
        self._log_flush_pending = False
        for ctrl, messages in list(self._log_buffer.values()):
            append_lines(ctrl, messages, self.syntax_highlight)
        self._log_buffer.clear()
        self._update_status_bar()

    def _clear_log(self) -> None:
        self._log_buffer.clear()
        self._log_flush_pending = False
        for ctrl in self._log_ctrls.values():
            clear_text(ctrl)
        self._warning_count = 0
        self._error_count = 0
        self._warning_messages.clear()
        self._error_messages.clear()
        self._update_status_bar()

    def _add_notebook_placeholder(self, notebook: wx.Notebook) -> wx.Panel:
        """GTK cannot layout an empty wx.Notebook; keep a stub page until real output exists."""
        page = wx.Panel(notebook)
        page.SetFont(self._ui_font)
        sizer = wx.BoxSizer(wx.VERTICAL)
        hint = wx.StaticText(
            page,
            label="Output appears here after Diagnose, Renew, or Apply.",
            style=wx.ALIGN_CENTER,
        )
        hint.SetFont(self._ui_font)
        sizer.AddStretchSpacer()
        sizer.Add(hint, 0, wx.ALIGN_CENTER | wx.LEFT | wx.RIGHT, 12)
        sizer.AddStretchSpacer()
        page.SetSizer(sizer)
        notebook.AddPage(page, "Output")
        return page

    def _adopt_notebook_placeholder(
        self,
        notebook: wx.Notebook,
        placeholder: wx.Panel | None,
        name: str,
    ) -> tuple[wx.Window, wx.Panel | None]:
        """Reuse the stub page as the first tab (avoid DeletePage — GTK a11y bug)."""
        if placeholder is None:
            ctrl = self._create_output_tab(notebook)
            notebook.AddPage(ctrl.GetParent(), name)
            return ctrl, None

        for child in list(placeholder.GetChildren()):
            child.Destroy()
        placeholder.SetSizer(None)
        ctrl = _make_text_ctrl(placeholder)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(ctrl, 1, wx.EXPAND | wx.ALL, 6)
        placeholder.SetSizer(sizer)
        placeholder.Layout()
        for index in range(notebook.GetPageCount()):
            if notebook.GetPage(index) is placeholder:
                notebook.SetPageText(index, name)
                break
        return ctrl, None

    def _create_output_tab(self, notebook: wx.Notebook) -> wx.Window:
        page = wx.Panel(notebook)
        page.SetFont(self._ui_font)
        sizer = wx.BoxSizer(wx.VERTICAL)
        ctrl = _make_text_ctrl(page)
        sizer.Add(ctrl, 1, wx.EXPAND | wx.ALL, 6)
        page.SetSizer(sizer)
        return ctrl

    def _ensure_log_tabs(self, profile_names: list[str], *, reset: bool = False) -> None:
        """Ensure log tabs exist; reset only the listed profiles."""
        notebook = self._log_notebook
        notebook.Freeze()
        try:
            for name in profile_names:
                ctrl = self._log_ctrls.get(name)
                if ctrl is None:
                    ctrl, self._log_notebook_placeholder = self._adopt_notebook_placeholder(
                        notebook,
                        self._log_notebook_placeholder,
                        name,
                    )
                    self._log_ctrls[name] = ctrl
                elif reset:
                    clear_text(ctrl)
        finally:
            notebook.Thaw()

    def _ensure_diag_tabs(self, profile_names: list[str], *, reset: bool = False) -> None:
        """Ensure diagnostic tabs exist; reset only the listed profiles."""
        notebook = self._diag_notebook
        notebook.Freeze()
        try:
            for name in profile_names:
                ctrl = self._diag_ctrls.get(name)
                if ctrl is None:
                    ctrl, self._diag_notebook_placeholder = self._adopt_notebook_placeholder(
                        notebook,
                        self._diag_notebook_placeholder,
                        name,
                    )
                    self._diag_ctrls[name] = ctrl
                elif reset:
                    clear_text(ctrl)
        finally:
            notebook.Thaw()

    def _layout_output_pane(self) -> None:
        if not self._main_split.IsSplit():
            return
        self._main_split.Update()
        self._right_panel.Layout()
        self._notebook.Layout()

    def _prepare_diagnose_output(self, profile_names: list[str]) -> None:
        self._prepare_output_panes(diagnostics=True, logging=True)
        self._layout_output_pane()
        self._prepare_output_tabs(profile_names, diagnostics=True)

    def _prepare_log_output(self, profile_names: list[str]) -> None:
        self._prepare_output_panes(diagnostics=False, logging=True)
        self._layout_output_pane()
        self._ensure_log_tabs(profile_names, reset=True)

    def _prepare_output_tabs(self, profile_names: list[str], *, diagnostics: bool = True) -> None:
        self._ensure_log_tabs(profile_names, reset=True)
        if diagnostics:
            self._ensure_diag_tabs(profile_names, reset=True)

    def _activate_profile_output_tabs(self, profile_name: str) -> None:
        """Select log and diagnostics notebook tabs for *profile_name*."""
        if not profile_name:
            return
        for notebook in (self._log_notebook, self._diag_notebook):
            for index in range(notebook.GetPageCount()):
                if notebook.GetPageText(index) == profile_name:
                    if notebook.GetSelection() != index:
                        notebook.SetSelection(index)
                    break

    def _append_profile_summary(self, profile_name: str, content: str) -> None:
        if not content:
            return
        ctrl = self._diag_ctrls.get(profile_name)
        if ctrl is None:
            return
        append_lines(ctrl, content.splitlines(), self.syntax_highlight)
