"""Namecheap account portal: login and manage API client-IP whitelist."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from chaddr.proxy import requests_proxies

_USER_AGENT = "Mozilla/5.0 (compatible; chaddr/1.0; +https://github.com/chaddr)"
_CSRF_RE = re.compile(r'<input type="hidden" id="x-ncpl-csrfvalue" value="(.+?)"')
_VALIDATION_ERROR_RE = re.compile(
    r"Validation Error</strong>\s+(.+?)</div>",
    re.IGNORECASE | re.DOTALL,
)
_SECOND_AUTH_PATH = "/myaccount/twofa/secondauth.aspx"
_WHITELIST_PAGE = "/settings/tools/apiaccess/whitelisted-ips"


class NamecheapPortalError(RuntimeError):
    """Portal login or whitelist operation failed."""


@dataclass(frozen=True)
class WhitelistedIp:
    name: str
    ip_address: str


def _www_host(sandbox: bool) -> str:
    prefix = "www.sandbox." if sandbox else "www."
    return f"https://{prefix}namecheap.com"


def _ap_host(sandbox: bool) -> str:
    prefix = "ap.www.sandbox." if sandbox else "ap.www."
    return f"https://{prefix}namecheap.com"


def _api_url(page: str) -> str:
    return f"/api/v1/ncpl/apiaccess/ui/{page}"


def _session(proxy: str | None) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT})
    proxies = requests_proxies(proxy)
    if proxies:
        session.proxies.update(proxies)
    return session


def _extract_csrf(html: str) -> str:
    match = _CSRF_RE.search(html)
    if not match:
        raise NamecheapPortalError("Could not find x-ncpl-csrfvalue token on the whitelist page.")
    return match.group(1)


def _check_portal_json(data: dict) -> dict:
    if data.get("__isError"):
        raise NamecheapPortalError(str(data.get("Message") or "portal error"))
    if not data.get("Success"):
        errors = data.get("Errors") or []
        messages = [
            str(item.get("Message") or item)
            for item in errors
            if isinstance(item, dict) or item
        ]
        raise NamecheapPortalError("; ".join(messages) or "portal API error")
    payload = data.get("Data")
    return payload if isinstance(payload, dict) else {}


def _login_www(session: requests.Session, username: str, password: str, sandbox: bool) -> None:
    host = _www_host(sandbox)
    session_handler = f"{host}/cart/ajax/SessionHandler.ashx"
    login_url = f"{host}/myaccount/login-signup/"

    key_response = session.post(session_handler, json={}, timeout=60)
    key_response.raise_for_status()
    try:
        session_key = key_response.json()["SessionKey"]
    except (ValueError, KeyError) as exc:
        raise NamecheapPortalError("Could not acquire Namecheap session key.") from exc

    if not session_key:
        raise NamecheapPortalError("Namecheap session key was empty.")

    login_response = session.post(
        login_url,
        data={
            "hidden_LoginPassword": "",
            "LoginUserName": username,
            "LoginPassword": password,
            "sessionEncryptValue": session_key,
        },
        timeout=60,
        allow_redirects=False,
    )

    status = login_response.status_code
    location = login_response.headers.get("Location", "")

    if status == 200:
        match = _VALIDATION_ERROR_RE.search(login_response.text)
        if match:
            detail = re.sub(r"<[^>]+>", "", match.group(1)).strip()
            raise NamecheapPortalError(detail or "Namecheap login validation failed.")
        raise NamecheapPortalError("Namecheap login did not redirect; check username and password.")

    if status in (301, 302, 303, 307, 308):
        if _SECOND_AUTH_PATH in location:
            raise NamecheapPortalError(
                "Namecheap account has two-factor authentication enabled. "
                "Disable 2FA or whitelist the client IP manually in the Namecheap dashboard."
            )
        return

    raise NamecheapPortalError(f"Unexpected Namecheap login response (HTTP {status}).")


def _portal_request(
    session: requests.Session,
    ap_host: str,
    page_path: str,
    token: str,
    payload: dict | None = None,
) -> dict:
    response = session.post(
        f"{ap_host}{page_path}",
        json=payload or {},
        headers={"x-ncpl-rcsrf": token},
        timeout=60,
    )
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise NamecheapPortalError("Namecheap portal returned non-JSON response.") from exc
    if not isinstance(data, dict):
        raise NamecheapPortalError("Namecheap portal returned unexpected payload.")
    return _check_portal_json(data)


def _fetch_csrf(session: requests.Session, ap_host: str) -> str:
    response = session.get(f"{ap_host}{_WHITELIST_PAGE}", timeout=60)
    response.raise_for_status()
    return _extract_csrf(response.text)


def get_whitelisted_ips(
    username: str,
    password: str,
    *,
    proxy: str | None = None,
    sandbox: bool = False,
) -> list[WhitelistedIp]:
    session = _session(proxy)
    _login_www(session, username, password, sandbox)
    ap_host = _ap_host(sandbox)
    token = _fetch_csrf(session, ap_host)
    data = _portal_request(session, ap_host, _api_url("GetWhitelistedIpAddresses"), token)
    rows = data.get("IpAddresses") or []
    result: list[WhitelistedIp] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Name") or "").strip()
        ip = str(row.get("IpAddress") or "").strip()
        if name and ip:
            result.append(WhitelistedIp(name=name, ip_address=ip))
    return result


def add_whitelisted_ip(
    username: str,
    password: str,
    ip_address: str,
    *,
    name: str | None = None,
    proxy: str | None = None,
    sandbox: bool = False,
) -> None:
    if not name:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        name = f"chaddr {stamp}".replace(":", "-")

    session = _session(proxy)
    _login_www(session, username, password, sandbox)
    ap_host = _ap_host(sandbox)
    token = _fetch_csrf(session, ap_host)
    _portal_request(
        session,
        ap_host,
        _api_url("AddIpAddress"),
        token,
        {
            "accountPassword": password,
            "ipAddress": ip_address,
            "name": name,
        },
    )


def ensure_client_ip_whitelisted(
    username: str,
    password: str,
    client_ip: str,
    *,
    proxy: str | None = None,
    sandbox: bool = False,
    entry_name: str | None = None,
    logger: logging.Logger | None = None,
) -> bool:
    """Return True if client_ip is already whitelisted or was added successfully."""
    log = logger or logging.getLogger("chaddr")
    existing = get_whitelisted_ips(username, password, proxy=proxy, sandbox=sandbox)
    for row in existing:
        if row.ip_address == client_ip:
            log.info("Namecheap API whitelist already contains client IP %s (%s)", client_ip, row.name)
            return True

    if len(existing) >= 20:
        raise NamecheapPortalError(
            "Namecheap whitelist is full (20 entries). Remove an entry in the dashboard first."
        )

    label = entry_name or "chaddr"
    log.info("Adding client IP %s to Namecheap API whitelist as %r", client_ip, label)
    add_whitelisted_ip(
        username,
        password,
        client_ip,
        name=label,
        proxy=proxy,
        sandbox=sandbox,
    )
    return True


def _truthy(value: str | None) -> bool:
    return value is not None and value.lower() not in ("", "0", "false", "no")


def is_portal_whitelist_enabled(options: dict[str, str]) -> bool:
    """Return True when programmatic whitelist updates via portal are allowed."""
    mode = (options.get("namecheap_whitelist") or "disabled").strip().lower()
    return mode not in ("disabled", "off", "0", "false", "no", "")


def update_whitelist_if_configured(
    options: dict[str, str],
    proxy: str | None,
    logger: logging.Logger | None = None,
) -> None:
    """Update Namecheap API whitelist when username, password, and client_ip are set."""
    if not is_portal_whitelist_enabled(options):
        return
    username = options.get("namecheap_username")
    password = options.get("namecheap_password")
    client_ip = options.get("client_ip")
    if not username or not password or not client_ip:
        return

    log = logger or logging.getLogger("chaddr")
    sandbox = _truthy(options.get("namecheap_sandbox"))
    entry_name = options.get("namecheap_whitelist_name") or None
    try:
        ensure_client_ip_whitelisted(
            username,
            password,
            client_ip,
            proxy=proxy,
            sandbox=sandbox,
            entry_name=entry_name,
            logger=log,
        )
    except NamecheapPortalError as exc:
        log.warning("Namecheap whitelist update failed: %s", exc)
    except requests.RequestException as exc:
        log.warning("Namecheap portal request failed: %s", exc)
