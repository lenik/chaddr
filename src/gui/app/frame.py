"""Main AddressEditFrame for the chaddr GUI."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import wx

from chaddr.gui.address_panel import AddressListPanel, _action_button_height, _btn_row_flags, _icon_button, BTN_ROW_BORDER
from chaddr.gui.history_panel import HistoryListPanel
from chaddr.gui.instructions_panel import InstructionsPanel
from chaddr.gui.theme import THEMES, mono_font, ui_font
from chaddr.profile import format_profile_dir_label
from chaddr.proxy import apply_proxy_env

from .frame_menus import FileMenuMixin
from .frame_ops import OperationsMixin
from .frame_output import OutputPaneMixin
from .frame_profiles import ProfileListMixin
from .frame_status import StatusBarMixin
from .ids import (
    ID_ABOUT,
    ID_APPLY,
    ID_BROWSE_PROFILE_DIR,
    ID_DIAGNOSE,
    ID_EDIT_PROFILE,
    ID_EXIT,
    ID_LOAD_CONFIG,
    ID_LOAD_PROFILE,
    ID_PREFERENCES,
    ID_REFRESH_PROFILES,
    ID_RENEW,
    ID_SYNTAX_HIGHLIGHT,
    ID_VIEW_RIGHT_PANE,
    THEME_MENU_IDS,
)
from .widgets import _append_menu_item, _art_bitmap


class AddressEditFrame(
    StatusBarMixin,
    OutputPaneMixin,
    OperationsMixin,
    ProfileListMixin,
    FileMenuMixin,
    wx.Frame,
):
    TAB_LOGGING = 0
    TAB_DIAGNOSTICS = 1

    def __init__(
        self,
        initial_profiles: list[str] | None = None,
        cli_options: dict | None = None,
        proxy: str | None = None,
        config_path: Path | None = None,
        old_ip: str | None = None,
    ) -> None:
        super().__init__(None, title="chaddr — Address Editor", size=(1100, 760))
        self.cli_options = cli_options or {}
        self._old_ip = old_ip or self.cli_options.get("old_ip")
        self.proxy = proxy
        self.config_path = config_path
        self.theme_name = "System"
        self.syntax_highlight = True
        self.logger = logging.getLogger("chaddr")
        self.logger.setLevel(logging.DEBUG)
        self._worker: threading.Thread | None = None
        self._proxy_backup = apply_proxy_env(proxy)
        self._current_action = "Ready"
        self._resource_count = 0
        self._warning_count = 0
        self._error_count = 0
        self._public_ip: str | None = None
        self._public_ip_loading = True
        self._main_split_sash_set = False
        self._profile_names: list[str] = []
        self._profile_labels: list[str] = []
        self._profile_status: dict[str, str] = {}
        self._log_ctrls: dict[str, wx.Window] = {}
        self._diag_ctrls: dict[str, wx.Window] = {}
        self._log_buffer: dict[int, tuple[wx.Window, list[str]]] = {}
        self._log_flush_pending = False
        self._address_fetch_token = 0
        self._address_fetch_thread: threading.Thread | None = None
        self._operation_cancel_event = threading.Event()
        self._ui_font = ui_font(10)
        self._mono_font = mono_font(10)

        self._build_menu()
        self._build_ui()
        self._build_status_bar()
        self._setup_logging()
        self._load_profile_list(initial_profiles or [])
        wx.CallAfter(self._seed_old_ip)
        self._apply_theme()
        self._update_status_bar()
        self._start_public_ip_fetch()
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _build_menu(self) -> None:
        menu_bar = wx.MenuBar()

        file_menu = wx.Menu()
        _append_menu_item(file_menu, ID_LOAD_PROFILE, "Load Profile\tCtrl+O", wx.ART_FILE_OPEN)
        _append_menu_item(file_menu, ID_BROWSE_PROFILE_DIR, "Browse...\tCtrl+Shift+O", wx.ART_FOLDER_OPEN)
        _append_menu_item(file_menu, ID_REFRESH_PROFILES, "Refresh\tCtrl+R", wx.ART_GO_DIR_UP)
        file_menu.AppendSeparator()
        _append_menu_item(file_menu, ID_LOAD_CONFIG, "Load Config\tCtrl+L", wx.ART_NORMAL_FILE)
        file_menu.AppendSeparator()
        _append_menu_item(file_menu, ID_EXIT, "Exit\tCtrl+Q", wx.ART_QUIT)
        menu_bar.Append(file_menu, "&File")

        edit_menu = wx.Menu()
        _append_menu_item(
            edit_menu,
            ID_EDIT_PROFILE,
            "Edit/Open with text editor\tCtrl+E",
            wx.ART_EDIT,
            help_string="Open selected profile files in the system text editor",
        )
        edit_menu.AppendSeparator()
        _append_menu_item(
            edit_menu,
            ID_PREFERENCES,
            "Preferences...\tCtrl+P",
            wx.ART_HELP_SETTINGS,
            help_string="Application preferences (proxy, etc.)",
        )
        menu_bar.Append(edit_menu, "&Edit")

        action_menu = wx.Menu()
        _append_menu_item(action_menu, ID_DIAGNOSE, "Diagnose\tCtrl+D", wx.ART_FIND)
        _append_menu_item(action_menu, ID_RENEW, "Renew\tCtrl+F11", wx.ART_REDO)
        _append_menu_item(action_menu, ID_APPLY, "Apply\tCtrl+G", wx.ART_TICK_MARK)
        menu_bar.Append(action_menu, "&Action")

        view_menu = wx.Menu()
        theme_menu = wx.Menu()
        for theme_id, name in zip(THEME_MENU_IDS, THEMES):
            theme_menu.AppendRadioItem(theme_id, name)
        theme_item = wx.MenuItem(view_menu, wx.ID_ANY, "Theme", subMenu=theme_menu)
        view_menu.Append(theme_item)
        _append_menu_item(
            view_menu,
            ID_VIEW_RIGHT_PANE,
            "Right Pane\tCtrl+H",
            wx.ART_HELP_SIDE_PANEL,
            kind=wx.ITEM_CHECK,
        )
        _append_menu_item(
            view_menu,
            ID_SYNTAX_HIGHLIGHT,
            "Syntax Highlight",
            wx.ART_REPORT_VIEW,
            kind=wx.ITEM_CHECK,
        )
        menu_bar.Append(view_menu, "&View")

        help_menu = wx.Menu()
        _append_menu_item(help_menu, ID_ABOUT, "About", wx.ART_INFORMATION)
        menu_bar.Append(help_menu, "&Help")

        self.SetMenuBar(menu_bar)
        menu_bar.Check(ID_SYNTAX_HIGHLIGHT, self.syntax_highlight)
        menu_bar.Check(ID_VIEW_RIGHT_PANE, False)
        menu_bar.Check(THEME_MENU_IDS[0], True)

        self.Bind(wx.EVT_MENU, self._on_load_profile, id=ID_LOAD_PROFILE)
        self.Bind(wx.EVT_MENU, self._on_browse_profile_dir, id=ID_BROWSE_PROFILE_DIR)
        self.Bind(wx.EVT_MENU, self._on_refresh_profiles, id=ID_REFRESH_PROFILES)
        self.Bind(wx.EVT_MENU, self._on_load_config, id=ID_LOAD_CONFIG)
        self.Bind(wx.EVT_MENU, lambda _e: self.Close(), id=ID_EXIT)
        self.Bind(wx.EVT_MENU, self._on_edit_profiles, id=ID_EDIT_PROFILE)
        self.Bind(wx.EVT_MENU, self._on_preferences, id=ID_PREFERENCES)
        self.Bind(wx.EVT_MENU, lambda _e: self._run_async("diagnose", self._do_diagnose), id=ID_DIAGNOSE)
        self.Bind(wx.EVT_MENU, lambda _e: self._run_async("renew", self._do_renew), id=ID_RENEW)
        self.Bind(wx.EVT_MENU, lambda _e: self._run_async("apply", self._do_apply), id=ID_APPLY)
        self.Bind(wx.EVT_MENU, self._on_toggle_syntax, id=ID_SYNTAX_HIGHLIGHT)
        self.Bind(wx.EVT_MENU, self._on_toggle_right_pane, id=ID_VIEW_RIGHT_PANE)
        self.Bind(wx.EVT_MENU, self._on_about, id=ID_ABOUT)
        for theme_id, name in zip(THEME_MENU_IDS, THEMES):
            self.Bind(wx.EVT_MENU, lambda e, n=name: self._set_theme(n), id=theme_id)

    def _build_ui(self) -> None:
        self.SetFont(self._ui_font)
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        self._main_split = wx.SplitterWindow(panel, style=wx.SP_LIVE_UPDATE | wx.SP_3D)
        self._main_split.SetMinimumPaneSize(280)
        self._main_split.SetSashGravity(0.5)

        self._control_panel = wx.Panel(self._main_split)
        self._control_panel.SetFont(self._ui_font)
        # Single address column; toolbar fits without squeezing GTK buttons.
        self._control_panel.SetMinSize((520, -1))

        control = wx.BoxSizer(wx.VERTICAL)

        profile_box = wx.StaticBox(self._control_panel)
        self.profile_box = profile_box
        profile_sizer = wx.StaticBoxSizer(profile_box, wx.VERTICAL)
        profile_header = wx.BoxSizer(wx.HORIZONTAL)
        self.profile_icon = wx.StaticBitmap(
            self._control_panel,
            bitmap=_art_bitmap(wx.ART_FOLDER, wx.ART_OTHER, 16),
        )
        self.profile_title = wx.StaticText(
            self._control_panel,
            label=format_profile_dir_label(),
        )
        self.profile_title.SetFont(self._ui_font)
        profile_header.Add(self.profile_icon, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        profile_header.Add(self.profile_title, 1, wx.ALIGN_CENTER_VERTICAL)
        self.profile_status_label = wx.StaticText(self._control_panel, label="")
        self.profile_status_label.SetFont(self._ui_font)
        profile_header.Add(self.profile_status_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        profile_sizer.Add(profile_header, 0, wx.EXPAND | wx.LEFT | wx.TOP | wx.RIGHT, 6)

        profile_row = wx.BoxSizer(wx.HORIZONTAL)
        profile_row.Add(
            wx.StaticText(self._control_panel, label="Profile:"),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            8,
        )
        self.profile_choice = wx.Choice(self._control_panel, choices=[])
        self.profile_choice.SetFont(self._ui_font)
        profile_row.Add(self.profile_choice, 1, wx.EXPAND)
        profile_sizer.Add(profile_row, 0, wx.EXPAND | wx.ALL, 6)
        control.Add(profile_sizer, 0, wx.EXPAND | wx.ALL, 8)

        self._detail_notebook = wx.Notebook(self._control_panel)
        instructions_page = wx.Panel(self._detail_notebook)
        instructions_page.SetFont(self._ui_font)
        instructions_sizer = wx.BoxSizer(wx.VERTICAL)
        self.instructions_panel = InstructionsPanel(instructions_page)
        instructions_sizer.Add(self.instructions_panel, 1, wx.EXPAND | wx.ALL, 4)
        instructions_page.SetSizer(instructions_sizer)

        addresses_page = wx.Panel(self._detail_notebook)
        addresses_page.SetFont(self._ui_font)
        addresses_sizer = wx.BoxSizer(wx.VERTICAL)
        self.address_panel = AddressListPanel(addresses_page)
        addresses_sizer.Add(self.address_panel, 1, wx.EXPAND | wx.ALL, 4)
        addresses_page.SetSizer(addresses_sizer)

        history_page = wx.Panel(self._detail_notebook)
        history_page.SetFont(self._ui_font)
        history_sizer = wx.BoxSizer(wx.VERTICAL)
        self.history_panel = HistoryListPanel(history_page)
        history_sizer.Add(self.history_panel, 1, wx.EXPAND | wx.ALL, 4)
        history_page.SetSizer(history_sizer)

        self._detail_notebook.AddPage(instructions_page, "Instructions")
        self._detail_notebook.AddPage(addresses_page, "Addresses")
        self._detail_notebook.AddPage(history_page, "History")
        self._detail_notebook.SetSelection(1)
        control.Add(self._detail_notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        action_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_height = _action_button_height(self._control_panel)
        action_row.AddStretchSpacer(1)
        self.diagnose_btn = _icon_button(self._control_panel, wx.ART_FIND, "Diagnose", btn_height)
        self.renew_btn = _icon_button(self._control_panel, wx.ART_REDO, "Renew", btn_height)
        self.apply_btn = _icon_button(self._control_panel, wx.ART_TICK_MARK, "Apply", btn_height)
        for btn in (self.diagnose_btn, self.renew_btn, self.apply_btn):
            action_row.Add(btn, 0, _btn_row_flags(), BTN_ROW_BORDER)
        control.Add(action_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self._control_panel.SetSizer(control)

        self._right_panel = wx.Panel(self._main_split)
        self._right_panel.SetFont(self._ui_font)
        self._right_panel.SetMinSize((360, -1))

        self._notebook = wx.Notebook(self._right_panel)
        log_page = wx.Panel(self._notebook)
        log_page.SetFont(self._ui_font)
        log_sizer = wx.BoxSizer(wx.VERTICAL)
        self._log_notebook = wx.Notebook(log_page)
        log_sizer.Add(self._log_notebook, 1, wx.EXPAND | wx.ALL, 6)
        log_page.SetSizer(log_sizer)
        self._log_notebook_placeholder = self._add_notebook_placeholder(self._log_notebook)

        diag_page = wx.Panel(self._notebook)
        diag_page.SetFont(self._ui_font)
        diag_sizer = wx.BoxSizer(wx.VERTICAL)
        self._diag_notebook = wx.Notebook(diag_page)
        diag_sizer.Add(self._diag_notebook, 1, wx.EXPAND | wx.ALL, 6)
        diag_page.SetSizer(diag_sizer)
        self._diag_notebook_placeholder = self._add_notebook_placeholder(self._diag_notebook)

        self._notebook.AddPage(log_page, "Logging")
        self._notebook.AddPage(diag_page, "Diagnostics")

        right_layout = wx.BoxSizer(wx.VERTICAL)
        right_layout.Add(self._notebook, 1, wx.EXPAND | wx.ALL, 4)
        self._right_panel.SetSizer(right_layout)

        root.Add(self._main_split, 1, wx.EXPAND)
        panel.SetSizer(root)

        self._panel = panel
        self._left_panel = self._control_panel

        self._main_split.SplitVertically(
            self._control_panel,
            self._right_panel,
            sashPosition=self._initial_main_sash(),
        )
        self._main_split.Unsplit(self._right_panel)
        self._main_split_sash_set = False

        self.Bind(wx.EVT_SHOW, self._on_frame_show)
        self.Bind(wx.EVT_SIZE, self._on_resize_splitter)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

        self.diagnose_btn.Bind(wx.EVT_BUTTON, lambda _evt: self._run_async("diagnose", self._do_diagnose))
        self.renew_btn.Bind(wx.EVT_BUTTON, lambda _evt: self._run_async("renew", self._do_renew))
        self.apply_btn.Bind(wx.EVT_BUTTON, lambda _evt: self._run_async("apply", self._do_apply))
        self.profile_choice.Bind(wx.EVT_CHOICE, self._on_profile_selection)
        self.address_panel.set_on_changed(self._refresh_action_buttons)
        self.address_panel.listbox.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda _evt: self._refresh_action_buttons())
        self.address_panel.listbox.Bind(wx.EVT_LIST_ITEM_DESELECTED, lambda _evt: self._refresh_action_buttons())
        self.instructions_panel.set_on_changed(self._refresh_action_buttons)
        self.history_panel.set_on_save(self._save_profile_history)

    def _initial_main_sash(self) -> int:
        min_pane = self._main_split.GetMinimumPaneSize()
        for width in (
            self._main_split.GetClientSize().GetWidth(),
            self.GetClientSize().GetWidth(),
            self.GetSize().GetWidth(),
            1100,
        ):
            if width > min_pane * 2:
                return max(width // 2, min_pane)
        return min_pane

    def _center_main_splitter(self) -> None:
        if not self._main_split.IsSplit():
            return
        width = self._main_split.GetClientSize().GetWidth()
        min_pane = self._main_split.GetMinimumPaneSize()
        if width <= min_pane * 2:
            return
        self._main_split.SetSashPosition(max(width // 2, min_pane))
        self._main_split_sash_set = True

    def _on_frame_show(self, evt) -> None:
        evt.Skip()
        if not self._main_split_sash_set:
            wx.CallAfter(self._center_main_splitter)

    def _on_char_hook(self, evt: wx.KeyEvent) -> None:
        key = evt.GetKeyCode()
        mods = evt.GetModifiers()
        ctrl = bool(mods & wx.MOD_CONTROL)
        alt = bool(mods & wx.MOD_ALT) and not ctrl

        if (ctrl or alt) and key in (wx.WXK_PAGEUP, getattr(wx, "WXK_NUMPAGEUP", -1)):
            self._cycle_detail_tab(-1)
            return
        if (ctrl or alt) and key in (wx.WXK_PAGEDOWN, getattr(wx, "WXK_NUMPAGEDOWN", -1)):
            self._cycle_detail_tab(1)
            return
        if (ctrl or alt) and key == wx.WXK_LEFT:
            self._cycle_profile(-1)
            return
        if (ctrl or alt) and key == wx.WXK_RIGHT:
            self._cycle_profile(1)
            return
        evt.Skip()

    def _cycle_detail_tab(self, delta: int) -> None:
        count = self._detail_notebook.GetPageCount()
        if count <= 0:
            return
        index = (self._detail_notebook.GetSelection() + delta) % count
        self._detail_notebook.SetSelection(index)

    def _cycle_profile(self, delta: int) -> None:
        count = self.profile_choice.GetCount()
        if count <= 0:
            return
        current = self.profile_choice.GetSelection()
        if current == wx.NOT_FOUND:
            current = 0 if delta > 0 else count - 1
        else:
            current = (current + delta) % count
        self.profile_choice.SetSelection(current)
        self._on_profile_selection(None)

    def _on_resize_splitter(self, evt) -> None:
        if not self._main_split_sash_set and self._main_split.IsSplit():
            self._center_main_splitter()
        evt.Skip()
