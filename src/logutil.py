"""Logging helpers for CLI and GUI."""

from __future__ import annotations

import logging
import sys
from typing import Callable


LogCallback = Callable[[str, str], None]  # (level, message)


class CallbackHandler(logging.Handler):
    def __init__(self, callback: LogCallback) -> None:
        super().__init__()
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.callback(record.levelname.lower(), msg)
        except Exception:
            self.handleError(record)


def setup_logger(name: str = "chaddr", callback: LogCallback | None = None) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    stream.setLevel(logging.INFO)
    logger.addHandler(stream)

    if callback:
        cb = CallbackHandler(callback)
        cb.setFormatter(fmt)
        logger.addHandler(cb)

    return logger
