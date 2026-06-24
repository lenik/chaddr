"""Simple syntax highlighting for log and diagnostic panes."""

from __future__ import annotations

import re

try:
    import wx.stc as stc
except ImportError:  # pragma: no cover
    stc = None

STYLE_DEFAULT = 0
STYLE_OK = 1
STYLE_FAIL = 2
STYLE_TIME = 3
STYLE_IP = 4
STYLE_GUIDE = 5
STYLE_HEADER = 6
STYLE_ACTION = 7
STYLE_PREVIEW = 8

IP_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b")


def setup_styles(ctrl) -> None:
    if stc is None or not hasattr(ctrl, "StyleSetForeground"):
        return
    ctrl.StyleClearAll()
    ctrl.StyleSetForeground(STYLE_DEFAULT, ctrl.GetForegroundColour())
    ctrl.StyleSetForeground(STYLE_OK, wx_colour(0, 128, 0))
    ctrl.StyleSetForeground(STYLE_FAIL, wx_colour(200, 40, 40))
    ctrl.StyleSetForeground(STYLE_TIME, wx_colour(120, 120, 120))
    ctrl.StyleSetForeground(STYLE_IP, wx_colour(0, 90, 180))
    ctrl.StyleSetForeground(STYLE_GUIDE, wx_colour(180, 110, 0))
    ctrl.StyleSetForeground(STYLE_HEADER, wx_colour(60, 60, 160))
    ctrl.StyleSetBold(STYLE_HEADER, True)
    ctrl.StyleSetForeground(STYLE_ACTION, wx_colour(140, 60, 180))
    ctrl.StyleSetForeground(STYLE_PREVIEW, wx_colour(120, 120, 120))
    if hasattr(ctrl, "SetCaretWidth"):
        ctrl.SetCaretWidth(0)
    if hasattr(ctrl, "SetCaretLineVisible"):
        ctrl.SetCaretLineVisible(False)


def wx_colour(r: int, g: int, b: int):
    import wx

    return wx.Colour(r, g, b)


def style_for_line(line: str) -> int:
    stripped = line.strip()
    if stripped.startswith(">"):
        return STYLE_PREVIEW
    if stripped.startswith("[Renew]") or stripped.startswith("[Apply]"):
        return STYLE_ACTION
    upper = line.upper()
    if "[OK]" in upper or "✓" in line:
        return STYLE_OK
    if "[FAIL]" in upper or "✗" in line or " ERROR" in upper or upper.endswith(" ERROR"):
        return STYLE_FAIL
    if " WARNING" in upper or upper.endswith(" WARNING"):
        return STYLE_GUIDE
    if line.strip().startswith("→") or "→" in line:
        return STYLE_GUIDE
    if line.startswith("Profile:") or line.startswith("Result:") or line.startswith("==="):
        return STYLE_HEADER
    if re.match(r"^\d{2}:\d{2}:\d{2}\s", line):
        if " ERROR" in upper or " CRITICAL" in upper:
            return STYLE_FAIL
        if " WARNING" in upper:
            return STYLE_GUIDE
        if " DEBUG" in upper:
            return STYLE_TIME
        if IP_RE.search(line):
            return STYLE_IP
        return STYLE_TIME
    if IP_RE.search(line):
        return STYLE_IP
    return STYLE_DEFAULT


def _select_all_output(ctrl) -> None:
    if stc is not None and hasattr(ctrl, "SelectAll"):
        ctrl.SelectAll()
        return
    if hasattr(ctrl, "SetSelection") and hasattr(ctrl, "GetLastPosition"):
        ctrl.SetSelection(0, ctrl.GetLastPosition())
    elif hasattr(ctrl, "SelectAll"):
        ctrl.SelectAll()


def copy_output_selection(ctrl) -> bool:
    """Copy the current selection to the clipboard; return True if anything copied."""
    import wx

    if stc is not None and hasattr(ctrl, "GetSelectionStart"):
        if ctrl.GetSelectionStart() == ctrl.GetSelectionEnd():
            return False
        if hasattr(ctrl, "Copy"):
            ctrl.Copy()
            return True
        text = ctrl.GetSelectedText()
    elif hasattr(ctrl, "GetStringSelection"):
        text = ctrl.GetStringSelection()
        if not text:
            return False
    else:
        return False

    if not text:
        return False
    if wx.TheClipboard.Open():
        wx.TheClipboard.SetData(wx.TextDataObject(text))
        wx.TheClipboard.Close()
        return True
    return False


def bind_output_text_shortcuts(ctrl) -> None:
    import wx

    def on_key(evt: wx.KeyEvent) -> None:
        if evt.ControlDown() and evt.GetKeyCode() == ord("C"):
            if copy_output_selection(ctrl):
                return
        if evt.ControlDown() and evt.GetKeyCode() == ord("A"):
            _select_all_output(ctrl)
            return
        evt.Skip()

    ctrl.Bind(wx.EVT_KEY_DOWN, on_key)


def _configure_log_stc(ctrl) -> None:
    """Read-only log pane: hide caret; allow select and copy."""
    if stc is None or not hasattr(ctrl, "SetReadOnly"):
        return
    ctrl.SetReadOnly(True)
    ctrl.SetMarginWidth(0, 0)
    ctrl.SetMarginWidth(1, 0)
    ctrl.SetMarginWidth(2, 0)
    ctrl.SetCaretWidth(0)
    ctrl.SetCaretLineVisible(False)
    if hasattr(ctrl, "SetUseHorizontalScrollBar"):
        ctrl.SetUseHorizontalScrollBar(False)
    setup_styles(ctrl)
    bind_output_text_shortcuts(ctrl)


def append_lines(ctrl, lines: list[str], syntax_highlight: bool) -> None:
    if not lines:
        return
    is_stc = stc is not None and hasattr(ctrl, "StartStyling")
    readonly = is_stc and ctrl.GetReadOnly()

    if is_stc:
        ctrl.Freeze()
    try:
        if readonly:
            ctrl.SetReadOnly(False)

        for line in lines:
            text = line.rstrip("\n") + "\n"
            if not is_stc or not syntax_highlight:
                ctrl.AppendText(text)
            else:
                start = ctrl.GetLength()
                ctrl.AppendText(text)
                ctrl.StartStyling(start)
                ctrl.SetStyling(len(text), style_for_line(line))

        if readonly:
            ctrl.SetReadOnly(True)

        _scroll_to_end(ctrl)
    finally:
        if is_stc:
            ctrl.Thaw()


def append_line(ctrl, line: str, syntax_highlight: bool) -> None:
    append_lines(ctrl, [line], syntax_highlight)


def clear_text(ctrl) -> None:
    is_stc = stc is not None and hasattr(ctrl, "ClearAll")
    readonly = is_stc and getattr(ctrl, "GetReadOnly", lambda: False)()
    if is_stc:
        ctrl.Freeze()
    try:
        if readonly:
            ctrl.SetReadOnly(False)
        if hasattr(ctrl, "ClearAll"):
            ctrl.ClearAll()
        else:
            ctrl.Clear()
        if readonly:
            ctrl.SetReadOnly(True)
    finally:
        if is_stc:
            ctrl.Thaw()


def _scroll_to_end(ctrl) -> None:
    """Scroll to the end without moving the caret (GotoPos steals/spams focus on GTK)."""
    if hasattr(ctrl, "GetLineCount") and hasattr(ctrl, "SetFirstVisibleLine"):
        ctrl.SetFirstVisibleLine(max(0, ctrl.GetLineCount() - 1))
        return
    if hasattr(ctrl, "GetLastPosition") and hasattr(ctrl, "ShowPosition"):
        ctrl.ShowPosition(ctrl.GetLastPosition())


def set_text(ctrl, content: str, syntax_highlight: bool) -> None:
    readonly = getattr(ctrl, "GetReadOnly", lambda: False)()
    if readonly and hasattr(ctrl, "SetReadOnly"):
        ctrl.SetReadOnly(False)

    if stc is None or not syntax_highlight or not hasattr(ctrl, "StartStyling"):
        ctrl.SetValue(content)
    else:
        ctrl.SetValue("")
        for line in content.splitlines():
            append_line(ctrl, line, True)

    if readonly and hasattr(ctrl, "SetReadOnly"):
        ctrl.SetReadOnly(True)
