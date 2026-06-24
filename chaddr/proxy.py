"""HTTP/SOCKS proxy configuration for API and CLI calls."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse


_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
)


def _normalize_proxy_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme or "http"
    if scheme == "socks":
        return f"socks5h://{parsed.netloc}{parsed.path}"
    return url


def apply_proxy_env(proxy: str | None) -> dict[str, str | None]:
    """Apply proxy to process env; return previous values for restore."""
    backup = {key: os.environ.get(key) for key in _PROXY_ENV_KEYS}
    if not proxy:
        return backup

    proxy_url = _normalize_proxy_url(proxy)
    for key in _PROXY_ENV_KEYS:
        os.environ[key] = proxy_url
    return backup


def restore_proxy_env(backup: dict[str, str | None]) -> None:
    for key, value in backup.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def log_proxy_hint(proxy: str | None) -> str:
    if proxy:
        return f"Proxy: {proxy}"
    return "Proxy: (none)"


def requests_proxies(proxy: str | None) -> dict[str, str] | None:
    if not proxy:
        return None
    url = _normalize_proxy_url(proxy)
    return {"http": url, "https": url}


def boto_config(proxy: str | None) -> Any | None:
    if not proxy:
        return None
    try:
        from botocore.config import Config
    except ImportError:
        return None

    url = _normalize_proxy_url(proxy)
    return Config(proxies={"http": url, "https": url})
