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


def append_line(ctrl, line: str, syntax_highlight: bool) -> None:
    text = line.rstrip("\n") + "\n"
    readonly = getattr(ctrl, "GetReadOnly", lambda: False)()
    if readonly and hasattr(ctrl, "SetReadOnly"):
        ctrl.SetReadOnly(False)

    if stc is None or not syntax_highlight or not hasattr(ctrl, "StartStyling"):
        ctrl.AppendText(text)
    else:
        start = ctrl.GetLength()
        ctrl.AppendText(text)
        ctrl.StartStyling(start)
        ctrl.SetStyling(len(text), style_for_line(line))

    if readonly and hasattr(ctrl, "SetReadOnly"):
        ctrl.SetReadOnly(True)

    _scroll_to_end(ctrl)


def clear_text(ctrl) -> None:
    readonly = getattr(ctrl, "GetReadOnly", lambda: False)()
    if readonly and hasattr(ctrl, "SetReadOnly"):
        ctrl.SetReadOnly(False)
    if hasattr(ctrl, "ClearAll"):
        ctrl.ClearAll()
    else:
        ctrl.Clear()
    if readonly and hasattr(ctrl, "SetReadOnly"):
        ctrl.SetReadOnly(True)


def _scroll_to_end(ctrl) -> None:
    if hasattr(ctrl, "GotoPos"):
        ctrl.GotoPos(ctrl.GetLength())
    elif hasattr(ctrl, "ShowPosition") and hasattr(ctrl, "GetLastPosition"):
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
