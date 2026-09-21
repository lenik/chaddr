"""Status bar, progress, public IP, and clipboard helpers for AddressEditFrame."""

from __future__ import annotations

import threading

import wx

from chaddr.config import cached_client_ip, resolve_client_ip

from .ids import PROGRESS_STOP_BTN_SIZE
from .widgets import _art_bitmap


class StatusBarMixin:
    def _build_status_bar(self) -> None:
        # wxSTB_SHOW_TIPS forbids StatusBar.SetToolTip(); drop it so we can
        # show the warn/error click hint on field 3.
        style = wx.STB_DEFAULT_STYLE & ~wx.STB_SHOW_TIPS
        self._status_bar = self.CreateStatusBar(4, style)
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
        self._status_bar.Bind(wx.EVT_LEFT_DOWN, self._on_status_bar_left_down)
        self._status_bar.Bind(wx.EVT_RIGHT_DOWN, self._on_status_bar_right_down)
        self._status_bar.Bind(wx.EVT_CONTEXT_MENU, self._on_status_bar_context_menu)

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
        # GTK emits negative-size warnings if we allocate into a collapsed field.
        if rect.width < 24 or rect.height < 8:
            return
        height = max(min(rect.height - 4, 16), 8)
        y = rect.y + max((rect.height - height) // 2, 0)
        stop_w = PROGRESS_STOP_BTN_SIZE + 2 if self._stop_btn.IsShown() else 0
        gap = 2 if stop_w else 0
        gauge_w = max(rect.width - stop_w - gap - 4, 16)
        self._progress.SetSize(gauge_w, height)
        self._progress.SetPosition((rect.x + 2, y))
        if self._stop_btn.IsShown():
            btn = min(PROGRESS_STOP_BTN_SIZE, max(rect.height - 2, 8))
            self._stop_btn.SetSize(btn, btn)
            self._stop_btn.SetPosition((rect.x + rect.width - btn - 2, y))
        self._status_bar.Refresh()

    def _show_progress(self, *, show_stop: bool = True) -> None:
        self._progress.SetValue(0)
        self._progress.Show()
        if show_stop:
            self._stop_btn.Show()
        else:
            self._stop_btn.Hide()
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
        cached = cached_client_ip(self.cli_options)
        if cached:
            self._public_ip = cached
            self._public_ip_loading = False
            self._public_ip_progress_active = False
            self.logger.info("Using cached client IP: %s", cached)
            self._update_status_bar()
            return

        self._public_ip_loading = True
        self._public_ip_progress_active = True
        self._public_ip_total = 0
        token = getattr(self, "_public_ip_fetch_token", 0) + 1
        self._public_ip_fetch_token = token
        wx.CallAfter(self._begin_public_ip_progress)

        def on_progress(done: int, total: int) -> None:
            if token != self._public_ip_fetch_token:
                return
            wx.CallAfter(self._on_public_ip_progress, token, done, total)

        def worker() -> None:
            try:
                ip, source = resolve_client_ip(
                    self.cli_options,
                    self.proxy,
                    self.config_path,
                    self.logger,
                    progress=on_progress,
                )
                if token != self._public_ip_fetch_token:
                    return
                if ip:
                    wx.CallAfter(self._on_public_ip_ready, token, ip, source)
                else:
                    wx.CallAfter(
                        self._on_public_ip_failed,
                        token,
                        RuntimeError("no public IP consensus"),
                    )
            except Exception as exc:
                if token != self._public_ip_fetch_token:
                    return
                wx.CallAfter(self._on_public_ip_failed, token, exc)

        threading.Thread(target=worker, daemon=True, name="public-ip-fetch").start()

    def _begin_public_ip_progress(self) -> None:
        if not self._public_ip_progress_active:
            return
        self._show_progress(show_stop=False)
        self._progress.SetValue(0)
        self._set_action("Determine public IP(s) for this host...")

    def _on_public_ip_progress(self, token: int, done: int, total: int) -> None:
        if token != getattr(self, "_public_ip_fetch_token", 0):
            return
        if not self._public_ip_progress_active:
            return
        self._public_ip_total = total
        if not self._progress.IsShown():
            self._show_progress(show_stop=False)
        fraction = 0 if total <= 0 else int(100 * done / total)
        self._progress.SetValue(max(0, min(100, fraction)))
        self._set_action(f"Determine public IP(s) for this host... ({done}/{total})")

    def _finish_public_ip_progress(self) -> None:
        was_active = self._public_ip_progress_active
        self._public_ip_progress_active = False
        if not was_active:
            return
        # Don't hide if another operation owns the progress bar.
        worker_alive = self._worker is not None and self._worker.is_alive()
        address_alive = (
            self._address_fetch_thread is not None and self._address_fetch_thread.is_alive()
        )
        if not worker_alive and not address_alive:
            self._hide_progress()

    def _on_public_ip_ready(self, token: int, ip: str, source: str) -> None:
        if token != getattr(self, "_public_ip_fetch_token", 0):
            return
        self._public_ip = ip
        self._public_ip_loading = False
        if not self.cli_options.get("client_ip"):
            self.cli_options["client_ip"] = ip
        expire = self.cli_options.get("client_ip_expire")
        if expire:
            self.logger.debug("Client IP expires at %s", expire)
        self.logger.info("Client IP (%s): %s", source, ip)
        self._finish_public_ip_progress()
        self._set_action("Ready")
        self._update_status_bar()

    def _on_public_ip_failed(self, token: int, exc: Exception) -> None:
        if token != getattr(self, "_public_ip_fetch_token", 0):
            return
        self._public_ip = None
        self._public_ip_loading = False
        self.cli_options.pop("client_ip", None)
        self.cli_options.pop("client_ip_expire", None)
        self.logger.warning("Could not fetch public IP: %s", exc)
        self._finish_public_ip_progress()
        self._set_action("No public IP")
        self._update_status_bar()

    def _client_ip_text(self) -> str:
        ip = self.cli_options.get("client_ip") or self._public_ip
        if ip:
            return f"🌐 {ip}"
        if self._public_ip_loading:
            return "🌐 …"
        return "🌐 —"

    def _remote_ip_value(self) -> str | None:
        ip = (self.cli_options.get("client_ip") or self._public_ip or "").strip()
        return ip or None

    def _copy_text_to_clipboard(self, text: str) -> bool:
        if not text:
            return False
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(text))
            finally:
                wx.TheClipboard.Close()
            return True
        return False

    def _notify_copied(self, ip: str) -> None:
        message = f"Copied {ip} to clipboard"
        self.logger.info(message)
        self._set_action(f"Copied {ip}")
        try:
            import wx.adv as wx_adv

            notice = wx_adv.NotificationMessage("chaddr", message, parent=self)
            if hasattr(wx_adv, "NOTIFICATION_MESSAGE_INFO"):
                notice.SetFlags(wx_adv.ICON_INFORMATION)
            notice.Show(timeout=3)
        except Exception:
            pass

    def _on_status_bar_left_down(self, evt: wx.MouseEvent) -> None:
        pos = evt.GetPosition()
        if self._status_field_contains(3, pos):
            rect = self._status_bar.GetFieldRect(3)
            # Left half of field 3 → warnings; right half → errors.
            if pos.x < rect.x + rect.width // 2:
                self._show_issue_list("Warnings", self._warning_messages)
            else:
                self._show_issue_list("Errors", self._error_messages)
            return
        evt.Skip()

    def _show_issue_list(self, title: str, messages: list[str]) -> None:
        if not messages:
            wx.MessageBox(f"No {title.lower()} recorded.", title, wx.OK | wx.ICON_INFORMATION)
            return
        body = "\n".join(f"{index}. {text}" for index, text in enumerate(messages, start=1))
        dialog = wx.Dialog(self, title=title, size=(560, 360))
        root = wx.BoxSizer(wx.VERTICAL)
        text = wx.TextCtrl(
            dialog,
            value=body,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP | wx.BORDER_SUNKEN,
        )
        root.Add(text, 1, wx.EXPAND | wx.ALL, 8)
        buttons = dialog.CreateButtonSizer(wx.OK)
        if buttons:
            root.Add(buttons, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        dialog.SetSizer(root)
        dialog.CentreOnParent()
        dialog.ShowModal()
        dialog.Destroy()

    def _status_field_contains(self, field: int, position: wx.Point) -> bool:
        try:
            rect = self._status_bar.GetFieldRect(field)
        except Exception:
            return False
        return rect.Contains(position)

    def _on_status_bar_right_down(self, evt: wx.MouseEvent) -> None:
        if self._status_field_contains(0, evt.GetPosition()):
            self._copy_remote_ip_from_status()
            return
        evt.Skip()

    def _on_status_bar_context_menu(self, evt: wx.ContextMenuEvent) -> None:
        position = evt.GetPosition()
        if position == wx.DefaultPosition:
            position = self._status_bar.ScreenToClient(wx.GetMousePosition())
        else:
            position = self._status_bar.ScreenToClient(position)
        if self._status_field_contains(0, position):
            self._copy_remote_ip_from_status()
            return
        evt.Skip()

    def _copy_remote_ip_from_status(self) -> None:
        ip = self._remote_ip_value()
        if not ip:
            self._set_action("No remote address to copy")
            return
        if self._copy_text_to_clipboard(ip):
            self._notify_copied(ip)
        else:
            self._set_action("Could not access clipboard")

    def _update_status_bar(self) -> None:
        left = f"{self._client_ip_text()}  ·  {self._current_action}"
        mid = f"📋 {self._resource_count}"
        right = f"⚠️ {self._warning_count}  ❌ {self._error_count}"
        self.SetStatusText(left, 0)
        self.SetStatusText("", 1)
        self.SetStatusText(mid, 2)
        self.SetStatusText(right, 3)
        tip = "Left-click ⚠️ for warnings, ❌ for errors"
        if self._warning_count or self._error_count:
            tip += f" ({self._warning_count} warn, {self._error_count} error)"
        self._status_bar.SetToolTip(tip)

    def _set_action(self, action: str) -> None:
        self._current_action = action
        wx.CallAfter(self._update_status_bar)
