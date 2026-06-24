"""Theme definitions for the chaddr GUI."""

from __future__ import annotations

import wx

THEMES = {
    "System": {
        "bg": None,
        "fg": None,
        "panel": None,
        "log_bg": None,
        "diag_bg": None,
    },
    "Light": {
        "bg": wx.Colour(250, 250, 250),
        "fg": wx.Colour(30, 30, 30),
        "panel": wx.Colour(245, 245, 245),
        "log_bg": wx.Colour(255, 255, 255),
        "diag_bg": wx.Colour(252, 252, 252),
    },
    "Dark": {
        "bg": wx.Colour(32, 32, 32),
        "fg": wx.Colour(220, 220, 220),
        "panel": wx.Colour(40, 40, 40),
        "log_bg": wx.Colour(28, 28, 28),
        "diag_bg": wx.Colour(36, 36, 36),
    },
}

# Sans-serif faces with reliable CJK glyph coverage (try in order).
CJK_SANS_FACES = (
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "WenQuanYi Zen Hei",
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Arial Unicode MS",
)

_base_gui_font: wx.Font | None = None


def _pick_cjk_sans_font(point_size: int) -> wx.Font:
    for face in CJK_SANS_FACES:
        font = wx.Font(
            point_size,
            wx.FONTFAMILY_SWISS,
            wx.FONTSTYLE_NORMAL,
            wx.FONTWEIGHT_NORMAL,
            faceName=face,
        )
        if not font.IsOk():
            continue
        # wx may accept the font object but substitute another face — check match.
        actual = font.GetFaceName()
        if actual and (face.lower() in actual.lower() or actual.lower() in face.lower()):
            return font

    font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
    if font.IsOk():
        font.SetPointSize(point_size)
        return font
    return wx.Font(point_size, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)


def install_default_gui_font(point_size: int = 10) -> wx.Font:
    """Set app-wide default GUI font; call once after wx.App creation."""
    global _base_gui_font
    font = _pick_cjk_sans_font(point_size)
    if hasattr(wx, "SetDefaultGuiFont"):
        wx.SetDefaultGuiFont(font)
    _base_gui_font = font
    return font


def ui_font(point_size: int = 10) -> wx.Font:
    if _base_gui_font is not None and _base_gui_font.IsOk():
        font = wx.Font(_base_gui_font)
        font.SetPointSize(point_size)
        return font
    return _pick_cjk_sans_font(point_size)


def spare_entry_colour() -> wx.Colour:
    return wx.Colour(160, 160, 160)


def spare_entry_css_colour() -> str:
    colour = spare_entry_colour()
    return f"rgb({colour.Red()}, {colour.Green()}, {colour.Blue()})"


def mono_font(point_size: int = 10) -> wx.Font:
    """Monospace font for IP address fields only."""
    for face in ("DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono CJK SC", "Consolas"):
        font = wx.Font(
            point_size,
            wx.FONTFAMILY_TELETYPE,
            wx.FONTSTYLE_NORMAL,
            wx.FONTWEIGHT_NORMAL,
            faceName=face,
        )
        if font.IsOk():
            return font
    return wx.Font(point_size, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)


def apply_theme(window: wx.Window, theme_name: str, widgets: dict[str, wx.Window]) -> None:
    theme = THEMES.get(theme_name, THEMES["System"])
    bg = theme["bg"]
    fg = theme["fg"]
    if bg is None:
        return

    window.SetBackgroundColour(bg)
    window.SetForegroundColour(fg)
    for key, widget in widgets.items():
        if widget is None:
            continue
        if key in ("log_ctrl", "summary_ctrl"):
            widget.SetBackgroundColour(theme.get("log_bg" if key == "log_ctrl" else "diag_bg", bg))
            widget.SetForegroundColour(fg)
        elif key == "panel" or key in ("left_panel", "right_panel"):
            widget.SetBackgroundColour(theme.get("panel", bg) or bg)
            widget.SetForegroundColour(fg)
        else:
            widget.SetBackgroundColour(bg)
            widget.SetForegroundColour(fg)
