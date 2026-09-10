"""Small wx widget helpers for the address editor GUI."""

from __future__ import annotations

import wx

try:
    import wx.stc as stc
except ImportError:  # pragma: no cover
    stc = None

from chaddr.gui.highlighter import bind_output_text_shortcuts, _configure_log_stc
from chaddr.gui.theme import mono_font


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
