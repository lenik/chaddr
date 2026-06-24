"""wx GUI for chaddr."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import wx

try:
    import wx.stc as stc
except ImportError:  # pragma: no cover
    stc = None

from chaddr import __version__
from chaddr.config import CONFIG_FILENAME, load_config, resolve_client_ip, save_config
from chaddr.address import AddressEntry, AddressSet, is_ipv4, is_ipv6, merge_address_entries, spare_sets_from_entries
from chaddr.gui.address_panel import AddressListPanel
from chaddr.gui.diagnostics_format import mutable_action_lines
from chaddr.types.hosts_file import APPLY_TARGETS_LABEL
from chaddr.gui.editor import open_in_system_editor
from chaddr.gui.highlighter import (
    append_line,
    append_lines,
    bind_output_text_shortcuts,
    clear_text,
    set_text,
    setup_styles,
    _configure_log_stc,
)
from chaddr.gui.theme import THEMES, apply_theme, install_default_gui_font, mono_font, ui_font
from chaddr.orchestrator import (
    ProfileRunResult,
    apply_address_profile,
    diagnose_profile,
    reallocate_profile,
)
from chaddr.profile import (
    ProfileAddressFetchEvent,
    display_profile_path,
    ensure_profile_dir,
    format_profile_dir_label,
    get_profile_dir,
    list_profile_items,
    iter_profile_address_fetch,
    instance_profile_addresses,
    load_profile,
    resolve_profile_addresses,
    set_profile_dir,
)
from chaddr.proxy import apply_proxy_env, restore_proxy_env
from chaddr.types.base import DiagnoseResult
from chaddr.types import get_handler_class

ID_LOAD_PROFILE = wx.NewIdRef()
ID_BROWSE_PROFILE_DIR = wx.NewIdRef()
ID_REFRESH_PROFILES = wx.NewIdRef()
ID_LOAD_CONFIG = wx.NewIdRef()
ID_EXIT = wx.NewIdRef()
ID_PREFERENCES = wx.NewIdRef()
ID_EDIT_PROFILE = wx.NewIdRef()
ID_DIAGNOSE = wx.NewIdRef()
ID_RENEW = wx.NewIdRef()
ID_APPLY = wx.NewIdRef()
ID_SYNTAX_HIGHLIGHT = wx.NewIdRef()
ID_VIEW_RIGHT_PANE = wx.NewIdRef()
ID_ABOUT = wx.NewIdRef()
THEME_MENU_IDS = [wx.NewIdRef() for _ in THEMES]

STATUS_BUSY = "🟡"
STATUS_FAIL = "🔴"
STATUS_OK = "🟢"

PROFILE_INDICATOR_WIDTH = 34
PROGRESS_STOP_BTN_SIZE = 18

_profile_log_context = threading.local()


class ProfileLogContext:
    """Route GUI log lines to the active profile tab in this thread."""

    def __init__(self, profile_name: str | None) -> None:
        self.profile_name = profile_name
        self._previous: str | None = None

    def __enter__(self) -> ProfileLogContext:
        self._previous = getattr(_profile_log_context, "name", None)
        _profile_log_context.name = self.profile_name
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _profile_log_context.name = self._previous


class GuiLogHandler(logging.Handler):
    def __init__(self, callback) -> None:
        super().__init__()
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            level = record.levelname.lower()
            profile_name = getattr(_profile_log_context, "name", None)
            wx.CallAfter(self.callback, message, level, profile_name)
        except Exception:
            self.handleError(record)


class _AggregateProgress:
    """Thread-safe progress across parallel profile operations."""

    def __init__(self, update: Callable[[float, str], None], names: list[str]) -> None:
        self._update = update
        self._fractions = {name: 0.0 for name in names}
        self._lock = threading.Lock()
        self._count = max(len(names), 1)

    def callback(self, name: str):
        def progress(fraction: float, message: str) -> None:
            with self._lock:
                self._fractions[name] = max(0.0, min(1.0, fraction))
                total = sum(self._fractions.values()) / self._count
            self._update(total, message)

        return progress


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
        if isinstance(parent, AddressEditFrame):
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


def _make_text_ctrl(parent: wx.Window, min_height: int = 200):
    if stc is not None:
        ctrl = stc.StyledTextCtrl(parent, style=wx.BORDER_SUNKEN)
        ctrl.SetFont(mono_font(10))
        _configure_log_stc(ctrl)
    else:
        ctrl = wx.TextCtrl(parent, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL | wx.BORDER_SUNKEN)
        ctrl.SetFont(mono_font(10))
        bind_output_text_shortcuts(ctrl)
    ctrl.SetMinSize((240, min_height))
    return ctrl


def _art_bitmap(art_id: str, client: int = wx.ART_MENU, size: int = 16) -> wx.Bitmap:
    bitmap = wx.ArtProvider.GetBitmap(art_id, client, (size, size))
    if bitmap.IsOk() and bitmap.GetSize() == wx.Size(size, size):
        return bitmap
    image = bitmap.ConvertToImage()
    if image.IsOk():
        scaled = image.Scale(size, size, wx.IMAGE_QUALITY_HIGH)
        if scaled.IsOk():
            return wx.Bitmap(scaled)
    return bitmap


def _menu_item_supports_bitmap(item: wx.MenuItem) -> bool:
    """GTK only allows bitmaps on plain menu items (not submenus or check/radio)."""
    if wx.Platform == "__WXGTK__":
        return item.GetKind() == wx.ITEM_NORMAL and item.GetSubMenu() is None
    return True


def _append_menu_item(
    menu: wx.Menu,
    item_id: int,
    label: str,
    art_id: str | None = None,
    *,
    kind: int = wx.ITEM_NORMAL,
    help_string: str = "",
) -> wx.MenuItem:
    item = wx.MenuItem(menu, item_id, label, help_string, kind)
    if art_id:
        bitmap = _art_bitmap(art_id)
        if bitmap.IsOk() and _menu_item_supports_bitmap(item):
            item.SetBitmap(bitmap)
    menu.Append(item)
    return item


class AddressEditFrame(wx.Frame):
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
        profile_sizer.Add(profile_header, 0, wx.EXPAND | wx.LEFT | wx.TOP | wx.RIGHT, 6)
        self.profile_list = wx.ListCtrl(
            self._control_panel,
            style=wx.LC_REPORT | wx.LC_NO_HEADER | wx.LC_HRULES,
        )
        self.profile_list.SetFont(self._ui_font)
        self.profile_list.SetMinSize((-1, 140))
        self.profile_list.InsertColumn(0, "Profile", width=280)
        self.profile_list.InsertColumn(
            1,
            "",
            width=PROFILE_INDICATOR_WIDTH,
            format=wx.LIST_FORMAT_RIGHT,
        )
        profile_sizer.Add(self.profile_list, 1, wx.EXPAND | wx.ALL, 6)
        control.Add(profile_sizer, 1, wx.EXPAND | wx.ALL, 8)
        self.profile_list.Bind(wx.EVT_SIZE, self._on_profile_list_size)
        self.profile_list.Bind(wx.EVT_KEY_DOWN, self._on_profile_list_key)

        address_box = wx.StaticBox(self._control_panel, label="Addresses")
        address_sizer = wx.StaticBoxSizer(address_box, wx.VERTICAL)
        self.address_panel = AddressListPanel(self._control_panel)
        address_sizer.Add(self.address_panel, 1, wx.EXPAND | wx.ALL, 6)
        control.Add(address_sizer, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.diagnose_btn = self.address_panel.diagnose_btn
        self.renew_btn = self.address_panel.renew_btn
        self.apply_btn = self.address_panel.apply_btn

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

        self.diagnose_btn.Bind(wx.EVT_BUTTON, lambda _evt: self._run_async("diagnose", self._do_diagnose))
        self.renew_btn.Bind(wx.EVT_BUTTON, lambda _evt: self._run_async("renew", self._do_renew))
        self.apply_btn.Bind(wx.EVT_BUTTON, lambda _evt: self._run_async("apply", self._do_apply))
        self.profile_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_profile_selection)
        self.profile_list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._on_profile_selection)
        self.address_panel.set_on_changed(self._refresh_action_buttons)
        self.address_panel.listbox.Bind(wx.EVT_LISTBOX, lambda _evt: self._refresh_action_buttons())

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
        wx.CallAfter(self._sync_profile_list_columns)

    def _on_resize_splitter(self, evt) -> None:
        if not self._main_split_sash_set and self._main_split.IsSplit():
            self._center_main_splitter()
        evt.Skip()

    def _on_toggle_right_pane(self, evt) -> None:
        if evt.IsChecked():
            if not self._main_split.IsSplit():
                self._main_split.SplitVertically(
                    self._control_panel,
                    self._right_panel,
                    sashPosition=self._initial_main_sash(),
                )
                self._main_split_sash_set = True
        elif self._main_split.IsSplit():
            self._main_split.Unsplit(self._right_panel)

    def _show_right_pane(self, *, tab: int | None = None) -> None:
        menu = self.GetMenuBar()
        if not self._main_split.IsSplit():
            menu.Check(ID_VIEW_RIGHT_PANE, True)
            self._main_split.SplitVertically(
                self._control_panel,
                self._right_panel,
                sashPosition=self._initial_main_sash(),
            )
            self._main_split_sash_set = True
        if tab is not None:
            self._notebook.SetSelection(tab)

    def _prepare_output_panes(self, *, diagnostics: bool = False, logging: bool = True) -> None:
        if diagnostics:
            self._show_right_pane(tab=self.TAB_DIAGNOSTICS)
        elif logging:
            self._show_right_pane(tab=self.TAB_LOGGING)

    def _build_status_bar(self) -> None:
        self._status_bar = self.CreateStatusBar(4)
        self._status_bar.SetStatusWidths([-3, 140, 100, 120])
        self._progress = wx.Gauge(self._status_bar, range=100, size=(130, 16))
        self._progress.SetValue(0)
        self._progress.Hide()
        stop_icon = _art_bitmap(wx.ART_CROSS_MARK, wx.ART_BUTTON, 14)
        self._stop_btn = wx.BitmapButton(
            self._status_bar,
            wx.ID_ANY,
            stop_icon,
            style=wx.BORDER_NONE,
            size=(PROGRESS_STOP_BTN_SIZE, PROGRESS_STOP_BTN_SIZE),
        )
        self._stop_btn.SetToolTip("Stop current operation")
        self._stop_btn.Hide()
        self._stop_btn.Bind(wx.EVT_BUTTON, self._on_stop_operation)
        self._status_bar.Bind(wx.EVT_SIZE, self._on_status_bar_size)

    def _on_status_bar_size(self, evt) -> None:
        if self._progress.IsShown() or self._stop_btn.IsShown():
            self._layout_status_bar_progress()
        evt.Skip()

    def _layout_status_bar_progress(self) -> None:
        if not hasattr(self, "_status_bar") or not hasattr(self, "_progress"):
            return
        if not self._progress.IsShown() and not self._stop_btn.IsShown():
            return
        rect = self._status_bar.GetFieldRect(1)
        height = max(rect.height - 4, 12)
        y = rect.y + max((rect.height - height) // 2, 1)
        stop_w = PROGRESS_STOP_BTN_SIZE + 2 if self._stop_btn.IsShown() else 0
        gap = 2 if stop_w else 0
        gauge_w = max(rect.width - stop_w - gap - 4, 20)
        self._progress.SetSize(gauge_w, height)
        self._progress.SetPosition((rect.x + 2, y))
        if self._stop_btn.IsShown():
            self._stop_btn.SetSize(PROGRESS_STOP_BTN_SIZE, PROGRESS_STOP_BTN_SIZE)
            self._stop_btn.SetPosition((rect.x + rect.width - PROGRESS_STOP_BTN_SIZE - 2, y))
        self._status_bar.Refresh()

    def _show_progress(self) -> None:
        self._progress.SetValue(0)
        self._progress.Show()
        self._stop_btn.Show()
        self._layout_status_bar_progress()

    def _hide_progress(self) -> None:
        self._progress.Hide()
        self._stop_btn.Hide()
        self._status_bar.Refresh()

    def _on_stop_operation(self, _evt: wx.CommandEvent) -> None:
        self._cancel_current_operation()

    def _cancel_current_operation(self) -> None:
        self._operation_cancel_event.set()
        self._address_fetch_token += 1
        wx.CallAfter(self._apply_operation_cancelled)

    def _apply_operation_cancelled(self) -> None:
        worker_alive = self._worker is not None and self._worker.is_alive()
        if worker_alive:
            self._set_action("Stopping...")
            return
        self._hide_progress()
        self._set_action("Cancelled")
        self._operation_cancel_event.clear()
        self._refresh_action_buttons()

    def _start_public_ip_fetch(self) -> None:
        def worker() -> None:
            try:
                ip, source = resolve_client_ip(
                    self.cli_options,
                    self.proxy,
                    self.config_path,
                    self.logger,
                )
                if ip:
                    wx.CallAfter(self._on_public_ip_ready, ip, source)
                else:
                    wx.CallAfter(self._on_public_ip_failed, RuntimeError("no client IP available"))
            except Exception as exc:
                wx.CallAfter(self._on_public_ip_failed, exc)

        threading.Thread(target=worker, daemon=True).start()

    def _on_public_ip_ready(self, ip: str, source: str) -> None:
        self._public_ip = ip
        self._public_ip_loading = False
        if not self.cli_options.get("client_ip"):
            self.cli_options["client_ip"] = ip
        expire = self.cli_options.get("client_ip_expire")
        if expire:
            self.logger.debug("Client IP expires at %s", expire)
        self.logger.info("Client IP (%s): %s", source, ip)
        self._update_status_bar()

    def _on_public_ip_failed(self, exc: Exception) -> None:
        self._public_ip_loading = False
        self.logger.warning("Could not fetch public IP: %s", exc)
        self._update_status_bar()

    def _client_ip_text(self) -> str:
        ip = self.cli_options.get("client_ip") or self._public_ip
        if ip:
            return f"🌐 {ip}"
        if self._public_ip_loading:
            return "🌐 …"
        return "🌐 —"

    def _update_status_bar(self) -> None:
        left = f"{self._client_ip_text()}  ·  {self._current_action}"
        mid = f"📋 {self._resource_count}"
        right = f"⚠️ {self._warning_count}  ❌ {self._error_count}"
        self.SetStatusText(left, 0)
        self.SetStatusText("", 1)
        self.SetStatusText(mid, 2)
        self.SetStatusText(right, 3)

    def _set_action(self, action: str) -> None:
        self._current_action = action
        wx.CallAfter(self._update_status_bar)

    def _count_resources(self, profiles: list[str]) -> int:
        total = 0
        for name in profiles:
            profile = load_profile(name)
            total += len(profile.entries)
            if profile.from_block is not None:
                total += 1
        return total

    def _profile_history_entries(self, profile_name: str) -> list[AddressEntry]:
        profile = load_profile(profile_name)
        entries: list[AddressEntry] = []
        for addr_set in profile.addr_history_sets():
            ip = addr_set.ipv4 or addr_set.ipv6
            if ip:
                entries.append(AddressEntry.from_history_ip(ip))
        return entries

    def _seed_old_ip(self) -> None:
        old_ip = (self._old_ip or "").strip()
        if not old_ip:
            return
        if not is_ipv4(old_ip) and not is_ipv6(old_ip):
            self.logger.warning("Ignoring invalid --old-ip: %s", old_ip)
            return
        entries = list(self.address_panel.get_entries())
        if any(entry.address == old_ip for entry in entries):
            return
        entries.append(AddressEntry.from_old_ip(old_ip))
        self.address_panel.set_entries(entries)

    def _seed_history(self) -> None:
        selected = self._selected_profiles()
        if len(selected) != 1:
            return
        entries = merge_address_entries(
            self.address_panel.get_entries(),
            self._profile_history_entries(selected[0]),
        )
        self.address_panel.set_entries(entries)

    def _add_instance_entries(self, ips: list[str]) -> None:
        incoming = [AddressEntry.from_instance_ip(ip) for ip in ips if is_ipv4(ip) or is_ipv6(ip)]
        if not incoming:
            return
        self.address_panel.set_entries(
            merge_address_entries(self.address_panel.get_entries(), incoming),
        )

    def _profile_spare_sets(self, profile_name: str) -> list[AddressSet]:
        profile = load_profile(profile_name)
        sets = list(profile.addr_history_sets())
        try:
            sets.append(resolve_profile_addresses(profile))
        except Exception:
            pass
        try:
            instance_addrs = instance_profile_addresses(
                profile,
                self.cli_options,
                self.proxy,
                self.logger,
            )
            if not instance_addrs.is_empty():
                sets.append(instance_addrs)
        except Exception:
            pass
        old_ip = self.cli_options.get("old_ip") or self._old_ip
        if old_ip:
            if is_ipv4(old_ip):
                sets.append(AddressSet(ipv4=old_ip))
            elif is_ipv6(old_ip):
                sets.append(AddressSet(ipv6=old_ip))
        sets.extend(spare_sets_from_entries(self.address_panel.get_entries()))
        return sets

    def _snapshot_spare_from_sets(self, profiles: list[str]) -> dict[str, list[AddressSet]]:
        """Capture per-profile spare sets on the GUI thread before background work."""
        return {name: self._profile_spare_sets(name) for name in profiles}

    def _refresh_action_buttons(self) -> None:
        selected = self._selected_profiles()
        if not selected:
            self.apply_btn.Enable(False)
            self.renew_btn.Enable(False)
            return

        has_manual = False
        has_reallocate = False
        for name in selected:
            profile = load_profile(name)
            has_manual = has_manual or profile.has_manual_types()
            for entry in profile.entries:
                handler_cls = get_handler_class(entry.type)
                if handler_cls and handler_cls.supports_reallocate:
                    has_reallocate = True

        self.renew_btn.Enable(has_reallocate)
        self.apply_btn.Enable(
            len(selected) == 1
            and has_manual
            and self.address_panel.has_valid_apply_selection()
        )

    def _refresh_addresses_from_profile(self) -> None:
        selected = self._selected_profiles()
        if len(selected) != 1:
            return

        profile_name = selected[0]
        self._operation_cancel_event.clear()
        self._address_fetch_token += 1
        token = self._address_fetch_token
        manual_entries = [
            entry for entry in self.address_panel.get_entries() if entry.source == "manual"
        ]

        wx.CallAfter(self._begin_address_fetch, token, manual_entries)

        def worker() -> None:
            try:
                profile = load_profile(profile_name)
                for event in iter_profile_address_fetch(
                    profile,
                    self.cli_options,
                    self.proxy,
                    self.logger,
                ):
                    if token != self._address_fetch_token:
                        return
                    wx.CallAfter(self._on_address_fetch_step, token, event)
            except Exception as exc:
                self.logger.warning("Address fetch failed for %s: %s", profile_name, exc)
            finally:
                wx.CallAfter(self._finish_address_fetch, token)

        self._address_fetch_thread = threading.Thread(target=worker, daemon=True, name="address-fetch")
        self._address_fetch_thread.start()

    def _begin_address_fetch(self, token: int, manual_entries: list[AddressEntry]) -> None:
        if token != self._address_fetch_token:
            return
        self.address_panel.set_entries(manual_entries)
        self._show_progress()
        self._progress.SetValue(0)
        self._set_action("Loading addresses...")

    def _on_address_fetch_step(self, token: int, event: ProfileAddressFetchEvent) -> None:
        if token != self._address_fetch_token:
            return
        self.address_panel.merge_entries(event.entries, replace_sources=event.replace_sources)
        self._progress.SetValue(max(0, min(100, int(event.fraction * 100))))
        self._set_action(event.message)
        self._refresh_action_buttons()

    def _finish_address_fetch(self, token: int) -> None:
        if token != self._address_fetch_token:
            return
        if self._operation_cancel_event.is_set():
            self._hide_progress()
            self._set_action("Cancelled")
            self._operation_cancel_event.clear()
            self._refresh_action_buttons()
            return
        if not (self._worker and self._worker.is_alive()):
            self._progress.SetValue(100)
            self._hide_progress()
            self._set_action("Ready")
        self._refresh_action_buttons()

    def _setup_logging(self) -> None:
        handler = GuiLogHandler(self._append_log)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        self.logger.addHandler(handler)

    def _append_log(self, message: str, level: str = "info", profile_name: str | None = None) -> None:
        if level == "warning":
            self._warning_count += 1
        elif level in ("error", "critical"):
            self._error_count += 1
        active = profile_name or getattr(_profile_log_context, "name", None)
        if active and active in self._log_ctrls:
            targets = [self._log_ctrls[active]]
        elif self._log_ctrls:
            targets = list(self._log_ctrls.values())
        else:
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
        ctrl = _make_text_ctrl(placeholder, min_height=400)
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
        ctrl = _make_text_ctrl(page, min_height=400)
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

    def _sync_profile_list_columns(self) -> None:
        client_width = self.profile_list.GetClientSize().GetWidth()
        if client_width <= PROFILE_INDICATOR_WIDTH:
            return
        self.profile_list.SetColumnWidth(1, PROFILE_INDICATOR_WIDTH)
        self.profile_list.SetColumnWidth(0, client_width - PROFILE_INDICATOR_WIDTH)

    def _on_profile_list_size(self, evt) -> None:
        self._sync_profile_list_columns()
        evt.Skip()

    def _on_profile_list_key(self, evt) -> None:
        if evt.GetModifiers() == wx.MOD_CONTROL and evt.GetKeyCode() == ord("A"):
            self._select_all_profiles()
            return
        evt.Skip()

    def _select_all_profiles(self) -> None:
        count = self.profile_list.GetItemCount()
        if count == 0:
            return
        self._select_profile_indices(list(range(count)))
        self._on_profile_selection(None)

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
        index = self._profile_index(name)
        if index is None:
            return
        self.profile_list.SetItem(index, 1, status)

    def _selected_profile_indices(self) -> list[int]:
        indices: list[int] = []
        item = self.profile_list.GetFirstSelected()
        while item != -1:
            indices.append(item)
            item = self.profile_list.GetNextSelected(item)
        return indices

    def _clear_profile_selection(self) -> None:
        for index in range(self.profile_list.GetItemCount()):
            self.profile_list.SetItemState(index, 0, wx.LIST_STATE_SELECTED)

    def _select_profile_indices(self, indices: list[int]) -> None:
        for index in indices:
            if 0 <= index < self.profile_list.GetItemCount():
                self.profile_list.SetItemState(
                    index,
                    wx.LIST_STATE_SELECTED,
                    wx.LIST_STATE_SELECTED,
                )

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
        self.profile_list.DeleteAllItems()
        for index, (name, label) in enumerate(zip(self._profile_names, self._profile_labels)):
            self.profile_list.InsertItem(index, label)
            self.profile_list.SetItem(index, 1, self._profile_status.get(name, ""))
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
        self._sync_profile_list_columns()
        self._resource_count = self._count_resources(self._selected_profiles())
        self._refresh_action_buttons()
        self._refresh_addresses_from_profile()
        self._update_status_bar()

    def _selected_profiles(self) -> list[str]:
        return [self._profile_names[i] for i in self._selected_profile_indices()]

    def _on_profile_selection(self, _evt) -> None:
        self._resource_count = self._count_resources(self._selected_profiles())
        self._refresh_action_buttons()
        self._refresh_addresses_from_profile()
        self._update_status_bar()

    def _refresh_manual_mode(self) -> None:
        self._refresh_action_buttons()

    def _set_busy(self, busy: bool) -> None:
        menu = self.GetMenuBar()
        for item_id in (ID_DIAGNOSE, ID_RENEW, ID_APPLY):
            menu.Enable(item_id, not busy)
        self.diagnose_btn.Enable(not busy)
        if not busy:
            self._refresh_manual_mode()
        else:
            self.renew_btn.Enable(False)
            self.apply_btn.Enable(False)

    def _update_progress(self, fraction: float, message: str) -> None:
        def _apply() -> None:
            self._progress.SetValue(int(fraction * 100))
            self._set_action(message)

        wx.CallAfter(_apply)

    def _run_async(self, label: str, func) -> None:
        if self._worker and self._worker.is_alive():
            wx.MessageBox("An operation is already running.", "Busy", wx.OK | wx.ICON_WARNING)
            return

        profiles = self._selected_profiles()
        if not profiles:
            wx.MessageBox("Select at least one profile.", "No profile", wx.OK | wx.ICON_INFORMATION)
            return

        self._warning_count = 0
        self._error_count = 0
        self._operation_cancel_event.clear()
        self._set_busy(True)
        wx.CallAfter(self._show_progress)
        self._set_action(f"Running {label}...")
        if label == "diagnose":
            self._prepare_diagnose_output(profiles)
            self._seed_history()
            for name in profiles:
                self._set_profile_status(name, STATUS_BUSY)
        elif label in ("renew", "apply"):
            self._prepare_log_output(profiles)
            if profiles:
                self._activate_profile_output_tabs(profiles[0])
            for name in profiles:
                self._set_profile_status(name, STATUS_BUSY)
        else:
            wx.CallAfter(lambda: self._prepare_output_panes(diagnostics=False, logging=True))
        for name in profiles:
            with ProfileLogContext(name):
                self.logger.info("=== %s: %s ===", label, name)

        spare_by_profile = self._snapshot_spare_from_sets(profiles)

        def worker() -> None:
            try:
                func(profiles, spare_by_profile)
            except Exception as exc:
                self.logger.exception("Operation failed: %s", exc)
                wx.CallAfter(wx.MessageBox, str(exc), "Error", wx.OK | wx.ICON_ERROR)
            finally:
                cancelled = self._operation_cancel_event.is_set()
                wx.CallAfter(self._finish_operation, "Cancelled" if cancelled else "Done")

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _finish_operation(self, label: str = "Done") -> None:
        cancelled = label == "Cancelled"
        self._operation_cancel_event.clear()
        self._set_busy(False)
        self._progress.SetValue(100 if not cancelled else 0)
        self._hide_progress()
        self._set_action(label)
        if cancelled:
            self.logger.info("Operation cancelled")

    def _do_diagnose(self, profiles: list[str], spare_by_profile: dict[str, list[AddressSet]]) -> None:
        if self._operation_cancel_event.is_set():
            return
        aggregate = _AggregateProgress(self._update_progress, profiles)
        results: dict[str, ProfileRunResult] = {}

        def diagnose_one(name: str) -> ProfileRunResult | None:
            if self._operation_cancel_event.is_set():
                return None
            wx.CallAfter(self._activate_profile_output_tabs, name)
            wx.CallAfter(self._append_profile_summary, name, f"=== Profile: {name} ===\n")
            profile = load_profile(name)

            def on_result(diag: DiagnoseResult, profile_name: str = name) -> None:
                if self._operation_cancel_event.is_set():
                    return
                text = self._format_diagnose_result(diag) + "\n"
                wx.CallAfter(
                    lambda pn=profile_name, content=text: self._append_profile_summary(pn, content),
                )

            try:
                with ProfileLogContext(name):
                    result = diagnose_profile(
                        profile,
                        self.cli_options,
                        self.proxy,
                        self.logger,
                        aggregate.callback(name),
                        spare_from_sets=spare_by_profile.get(name, []),
                        on_result=on_result,
                    )
            except Exception as exc:
                with ProfileLogContext(name):
                    self.logger.exception("Diagnose failed for %s: %s", name, exc)
                result = ProfileRunResult(name, False, str(exc))

            if self._operation_cancel_event.is_set():
                return result
            wx.CallAfter(self._append_profile_summary, name, f"Result: {result.message}\n\n")
            wx.CallAfter(
                self._set_profile_status,
                name,
                STATUS_OK if result.ok else STATUS_FAIL,
            )
            return result

        with ThreadPoolExecutor(max_workers=max(1, len(profiles))) as executor:
            futures = {}
            for name in profiles:
                if self._operation_cancel_event.is_set():
                    break
                futures[executor.submit(diagnose_one, name)] = name
            for future in as_completed(futures):
                if self._operation_cancel_event.is_set():
                    break
                name = futures[future]
                result = future.result()
                if result is not None:
                    results[name] = result

        if not self._operation_cancel_event.is_set():
            wx.CallAfter(self._apply_diagnose_batch_results, profiles, results)

    def _apply_diagnose_batch_results(
        self,
        profiles: list[str],
        results: dict[str, ProfileRunResult],
    ) -> None:
        all_ok = True
        instance_ips: list[str] = []
        for name in profiles:
            result = results.get(name)
            if result is None:
                continue
            with ProfileLogContext(name):
                self._log_diagnose(result)
            all_ok = all_ok and result.ok
            if len(profiles) == 1:
                for diag in result.diagnose_results:
                    if diag.type_name in ("aws elastic ip", "aliyun elastic ip"):
                        instance_ips.extend(diag.addresses)
        if instance_ips:
            wx.CallAfter(self._add_instance_entries, instance_ips)
        summary = "All profiles OK" if all_ok else "Some profiles have issues"
        self.logger.info("=== Diagnose summary: %s ===", summary)

    def _format_diagnose_result(self, diag: DiagnoseResult) -> str:
        lines: list[str] = []
        status = "OK" if diag.ok else "FAIL"
        lines.append(f"[{status}] {diag.type_name}: {diag.summary}")
        lines.extend(mutable_action_lines(diag))
        for item in diag.items:
            if item.label == APPLY_TARGETS_LABEL:
                continue
            mark = "OK" if item.ok else "FAIL"
            lines.append(f"  [{mark}] {item.label}: {item.detail}")
            if not item.ok and item.guidance:
                lines.append(f"      -> {item.guidance}")
        if diag.addresses and _show_diagnose_address_footer(diag):
            lines.append(f"    IP: {', '.join(diag.addresses)}")
        return "\n".join(lines)

    def _format_diagnose(self, result: ProfileRunResult) -> str:
        lines = [f"Profile: {result.profile_name}", f"Result: {result.message}", ""]
        if result.source_addresses and not result.source_addresses.is_empty():
            lines.append(f"From-source: {result.source_addresses.format()}")
            lines.append("")
        for diag in result.diagnose_results:
            lines.append(self._format_diagnose_result(diag))
        return "\n".join(lines)

    def _log_diagnose(self, result: ProfileRunResult) -> None:
        self.logger.info("Profile %s: %s", result.profile_name, result.message)
        for diag in result.diagnose_results:
            status = "OK" if diag.ok else "FAIL"
            self.logger.info("  [%s] %s - %s", status, diag.type_name, diag.summary)
            for item in diag.items:
                prefix = "OK" if item.ok else "FAIL"
                self.logger.info("    [%s] %s: %s", prefix, item.label, item.detail)
                if not item.ok and item.guidance:
                    self.logger.info("      -> %s", item.guidance)

    def _do_renew(self, profiles: list[str], spare_by_profile: dict[str, list[AddressSet]]) -> None:
        aggregate = _AggregateProgress(self._update_progress, profiles)
        try:
            override = self.address_panel.get_apply_address_set()
        except ValueError:
            override = None

        for name in profiles:
            if self._operation_cancel_event.is_set():
                break
            wx.CallAfter(self._activate_profile_output_tabs, name)
            profile = load_profile(name)
            try:
                with ProfileLogContext(name):
                    result = reallocate_profile(
                        profile,
                        override,
                        self.cli_options,
                        self.proxy,
                        self.logger,
                        aggregate.callback(name),
                        spare_from_sets=spare_by_profile.get(name, []),
                    )
            except Exception as exc:
                with ProfileLogContext(name):
                    self.logger.exception("Renew failed for %s: %s", name, exc)
                wx.CallAfter(self._set_profile_status, name, STATUS_FAIL)
                continue

            if result.ok:
                with ProfileLogContext(name):
                    self.logger.info("Profile %s: %s", name, result.message)
                wx.CallAfter(self._set_profile_status, name, STATUS_OK)
                if result.new_addresses and not result.new_addresses.is_empty():
                    wx.CallAfter(
                        self._add_instance_entries,
                        result.new_addresses.all(),
                    )
                    wx.CallAfter(self._seed_history)
            else:
                with ProfileLogContext(name):
                    self.logger.error("Profile %s failed: %s", name, result.message)
                wx.CallAfter(self._set_profile_status, name, STATUS_FAIL)

    def _do_apply(self, profiles: list[str], spare_by_profile: dict[str, list[AddressSet]]) -> None:
        if len(profiles) != 1:
            wx.CallAfter(
                wx.MessageBox,
                "Select exactly one profile for address change.",
                "Profile",
                wx.OK | wx.ICON_WARNING,
            )
            return

        try:
            new_addresses = self.address_panel.get_apply_address_set()
        except ValueError as exc:
            wx.CallAfter(wx.MessageBox, str(exc), "Invalid selection", wx.OK | wx.ICON_WARNING)
            return

        name = profiles[0]
        if self._operation_cancel_event.is_set():
            return
        wx.CallAfter(self._activate_profile_output_tabs, name)
        profile = load_profile(name)

        def progress(fraction: float, message: str) -> None:
            self._update_progress(fraction, message)

        with ProfileLogContext(name):
            spare_sets = spare_by_profile.get(name, [])
            self.logger.info(
                "Apply %s: selected %s (%d spare set(s) from panel/profile)",
                name,
                new_addresses.format(),
                len(spare_sets),
            )
            for index, addr_set in enumerate(spare_sets, start=1):
                if not addr_set.is_empty():
                    self.logger.info("  spare[%d]: %s", index, addr_set.format())
            result = apply_address_profile(
                profile,
                new_addresses,
                self.cli_options,
                self.proxy,
                self.logger,
                progress,
                spare_from_sets=spare_by_profile.get(name, []),
            )
        if result.ok:
            wx.CallAfter(self._set_profile_status, name, STATUS_OK)
            with ProfileLogContext(name):
                self.logger.info("Profile %s: %s", name, result.message)
                if result.old_addresses and result.new_addresses:
                    self.logger.info(
                        "  old: %s  new: %s",
                        result.old_addresses.format(),
                        result.new_addresses.format(),
                    )
        else:
            wx.CallAfter(self._set_profile_status, name, STATUS_FAIL)
            with ProfileLogContext(name):
                self.logger.error("Profile %s failed: %s", name, result.message)
                for diag in result.diagnose_results:
                    self.logger.error("  [%s] %s: %s", diag.type_name, diag.summary, ", ".join(diag.addresses))

    def apply_proxy(self, proxy: str | None, save_to_config: bool = False) -> None:
        self.proxy = proxy
        restore_proxy_env(self._proxy_backup)
        self._proxy_backup = apply_proxy_env(proxy)
        if save_to_config and self.config_path:
            save_config(self.config_path, {"proxy": proxy or ""})
            self.logger.info("Saved proxy to %s", self.config_path)
        self._public_ip_loading = True
        self._update_status_bar()
        self._start_public_ip_fetch()
        self._set_action("Proxy updated")

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
                "Select one or more profiles to open in a text editor.",
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


def _show_diagnose_address_footer(diag: DiagnoseResult) -> bool:
    """Omit redundant IP footer when addresses are already listed in diagnose items."""
    if diag.type_name in ("zone file", "bind db"):
        return False
    if diag.type_name == "hosts file":
        return False
    if diag.type_name == "file":
        return False
    return True


def run_gui(
    profiles: list[str] | None = None,
    cli_options: dict | None = None,
    proxy: str | None = None,
    config_path: Path | None = None,
    old_ip: str | None = None,
) -> None:
    ensure_profile_dir()
    app = wx.App(False)
    install_default_gui_font(10)
    frame = AddressEditFrame(profiles, cli_options, proxy, config_path, old_ip=old_ip)
    frame.Show()
    app.MainLoop()
