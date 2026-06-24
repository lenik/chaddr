"""Fetch public (egress) IPv4 via multiple echo services (concurrent)."""

from __future__ import annotations

import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from chaddr.proxy import requests_proxies

# (name, url) — plain-text IPv4 echo endpoints
IPV4_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("ipify", "https://api.ipify.org?format=text"),
    ("icanhazip", "https://icanhazip.com"),
    ("ifconfig.me", "https://ifconfig.me/ip"),
    ("aws-checkip", "https://checkip.amazonaws.com"),
    ("ipecho", "https://ipecho.net/plain"),
    ("ident.me", "https://v4.ident.me"),
    ("myexternalip", "https://myexternalip.com/raw"),
    ("seeip", "https://api.seeip.org"),
)


def _parse_ipv4(text: str) -> str:
    ip = text.strip().splitlines()[0].strip()
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise RuntimeError(f"invalid IP: {ip!r}") from exc
    if addr.version != 4:
        raise RuntimeError(f"expected IPv4, got: {ip}")
    return ip


def _fetch_from_provider(name: str, url: str, proxy: str | None, timeout: float) -> tuple[str, str]:
    response = requests.get(
        url,
        timeout=timeout,
        proxies=requests_proxies(proxy),
        headers={"User-Agent": "chaddr/1.0"},
    )
    response.raise_for_status()
    ip = _parse_ipv4(response.text)
    return name, ip


def fetch_public_ip(proxy: str | None = None, timeout: float = 5.0) -> tuple[str, str]:
    """Return (ipv4, provider_name). Raises if all providers fail."""
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(IPV4_PROVIDERS)) as pool:
        future_map = {
            pool.submit(_fetch_from_provider, name, url, proxy, timeout): name
            for name, url in IPV4_PROVIDERS
        }
        for future in as_completed(future_map):
            provider = future_map[future]
            try:
                name, ip = future.result()
                return ip, name
            except Exception as exc:
                errors.append(f"{provider}: {exc}")

    raise RuntimeError("all public-IP providers failed: " + "; ".join(errors))
