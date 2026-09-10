"""Logging context, GUI log handler, and aggregate progress helpers."""

from __future__ import annotations

import logging
import threading
from typing import Callable

import wx

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


class AggregateProgress:
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
