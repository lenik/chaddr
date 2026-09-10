"""Menu IDs and status/progress constants for the GUI."""

from __future__ import annotations

import wx

from chaddr.gui.theme import THEMES

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

PROGRESS_STOP_BTN_SIZE = 18
