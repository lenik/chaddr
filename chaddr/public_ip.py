"""Fetch public (egress) IPv4 via multiple echo services."""

from __future__ import annotations

import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from chaddr.proxy import requests_proxies

# Ordered by reliability / IPv4 specificity. Tried in waves: first wave races
# preferred providers; later waves only run if earlier ones all fail.
IPV4_PROVIDER_WAVES: tuple[tuple[tuple[str, str], ...], ...] = (
    (
        ("aws-checkip", "https://checkip.amazonaws.com"),
        ("ipify", "https://api4.ipify.org"),
        ("icanhazip", "https://ipv4.icanhazip.com"),
        ("ident.me", "https://v4.ident.me"),
    ),
    (
        ("ipsimple", "https://api.ipsimple.org/ipv4"),
        ("ip.sb", "https://api-ipv4.ip.sb/ip"),
        ("seeip", "https://ipv4.seeip.org"),
        ("ifconfig.me", "https://ifconfig.me/ip"),
        ("ipinfo", "https://ipinfo.io/ip"),
    ),
    (
        ("ifconfig.co", "https://ifconfig.co/ip"),
        ("ipecho", "https://ipecho.net/plain"),
        ("myexternalip", "https://myexternalip.com/raw"),
        ("ipaddr.me", "https://ipv4.ipaddr.me"),
        ("wtfismyip", "https://ipv4.wtfismyip.com/text"),
        ("geojs", "https://get.geojs.io/v1/ip"),
        ("ipapi", "https://ipapi.co/ip"),
        ("cloudflare-trace", "https://1.1.1.1/cdn-cgi/trace"),
        ("opendns-myip", "https://diagnostic.opendns.com/myip"),
    ),
)

# Flat tuple kept for callers / tests that expect a single provider list.
IPV4_PROVIDERS: tuple[tuple[str, str], ...] = tuple(
    provider for wave in IPV4_PROVIDER_WAVES for provider in wave
)


def _parse_ipv4(text: str) -> str:
    raw = text.strip()
    # Cloudflare /cdn-cgi/trace returns key=value lines including ip=...
    if "ip=" in raw and "\n" in raw:
        for line in raw.splitlines():
            if line.startswith("ip="):
                raw = line.split("=", 1)[1].strip()
                break
    ip = raw.splitlines()[0].strip()
    # Some providers prefix with "Current IP Address: "
    if ":" in ip and not ip.replace(".", "").isdigit():
        maybe = ip.rsplit(":", 1)[-1].strip()
        if maybe.count(".") == 3:
            ip = maybe
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
        headers={"User-Agent": "chaddr/1.0", "Accept": "text/plain"},
    )
    response.raise_for_status()
    ip = _parse_ipv4(response.text)
    return name, ip


def fetch_public_ip(proxy: str | None = None, timeout: float = 4.0) -> tuple[str, str]:
    """Return (ipv4, provider_name). Raises if all providers fail.

    Providers are tried in priority waves. Within a wave, requests race and the
    first valid IPv4 wins. The next wave runs only when the current wave fails.
    """
    errors: list[str] = []
    for wave in IPV4_PROVIDER_WAVES:
        with ThreadPoolExecutor(max_workers=len(wave)) as pool:
            future_map = {
                pool.submit(_fetch_from_provider, name, url, proxy, timeout): name
                for name, url in wave
            }
            for future in as_completed(future_map):
                provider = future_map[future]
                try:
                    name, ip = future.result()
                    return ip, name
                except Exception as exc:
                    errors.append(f"{provider}: {exc}")

    raise RuntimeError("all public-IP providers failed: " + "; ".join(errors))
