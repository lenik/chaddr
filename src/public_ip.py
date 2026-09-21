"""Fetch public (egress) IPv4 via multiple echo services."""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable

import requests

from chaddr.proxy import requests_proxies

# Ordered by reliability / IPv4 specificity. Started with a small stagger;
# replies within the total deadline are majority-voted.
IPV4_PROVIDERS: tuple[tuple[str, str], ...] = (
    # Prefer providers reachable without blocked anycast (1.1.1.1 is often filtered).
    ("aws-checkip", "https://checkip.amazonaws.com"),
    ("icanhazip", "https://ipv4.icanhazip.com"),
    ("ipinfo", "https://ipinfo.io/ip"),
    ("ifconfig.me", "https://ifconfig.me/ip"),
    ("ip.sb", "https://api-ipv4.ip.sb/ip"),
    ("ident.me", "https://v4.ident.me"),
    ("ipecho", "https://ipecho.net/plain"),
    ("myexternalip", "https://myexternalip.com/raw"),
    ("ipify", "https://api4.ipify.org"),
    ("ipsimple", "https://api.ipsimple.org/ipv4"),
    ("seeip", "https://ipv4.seeip.org"),
    ("ifconfig.co", "https://ifconfig.co/ip"),
    ("ipaddr.me", "https://ipv4.ipaddr.me"),
    ("wtfismyip", "https://ipv4.wtfismyip.com/text"),
    ("geojs", "https://get.geojs.io/v1/ip"),
    ("ipapi", "https://ipapi.co/ip"),
    ("opendns-myip", "https://diagnostic.opendns.com/myip"),
)

# Back-compat alias for older imports that mentioned waves.
IPV4_PROVIDER_WAVES: tuple[tuple[tuple[str, str], ...], ...] = (IPV4_PROVIDERS,)

DEFAULT_TOTAL_TIMEOUT = 20.0
DEFAULT_STAGGER = 0.05
# Cap each attempt so a DNS hang cannot burn the whole budget silently.
# ``requests`` timeouts do not cover getaddrinfo; we enforce a wall clock too.
DEFAULT_PER_REQUEST_CAP = 5.0

ProgressCallback = Callable[[int, int], None]


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
    # Separate connect/read so a stuck connect fails sooner than a slow body.
    connect = max(min(timeout, 3.0), 0.05)
    read = max(timeout, 0.05)
    response = requests.get(
        url,
        timeout=(connect, read),
        proxies=requests_proxies(proxy),
        headers={"User-Agent": "chaddr/1.0", "Accept": "text/plain"},
    )
    response.raise_for_status()
    ip = _parse_ipv4(response.text)
    return name, ip


def _fetch_with_wall_clock(
    name: str,
    url: str,
    proxy: str | None,
    timeout: float,
) -> tuple[str, str]:
    """Run the HTTP fetch in a daemon thread so DNS hangs cannot outlive *timeout*.

    ``requests``/urllib3 timeouts do not always cover ``getaddrinfo``; without a
    wall-clock join, a provider can sit in DNS for ~20s and never report an error
    before the overall deadline closes the result set.
    """
    box: dict[str, object] = {}

    def run() -> None:
        try:
            box["ok"] = _fetch_from_provider(name, url, proxy, timeout)
        except Exception as exc:  # noqa: BLE001 — surfaced to caller
            box["err"] = exc

    thread = threading.Thread(target=run, name=f"public-ip-http-{name}", daemon=True)
    thread.start()
    thread.join(timeout + 0.25)
    if "ok" in box:
        return box["ok"]  # type: ignore[return-value]
    if "err" in box:
        raise box["err"]  # type: ignore[misc]
    raise TimeoutError(f"no reply within {timeout:.1f}s (DNS/connect hang?)")


def _pick_majority(votes: list[tuple[str, str]]) -> tuple[str, str] | None:
    """Return (ip, 'provider1+provider2+...') for a unique top vote, else None."""
    if not votes:
        return None
    counts = Counter(ip for _name, ip in votes)
    ranked = counts.most_common()
    winner_ip, winner_count = ranked[0]
    if winner_count <= 0:
        return None
    if len(ranked) > 1 and ranked[1][1] == winner_count:
        return None
    sources = "+".join(name for name, ip in votes if ip == winner_ip)
    return winner_ip, sources


def _majority_is_decisive(votes: list[tuple[str, str]], pending: int) -> bool:
    """True when remaining replies cannot overturn a unique leader."""
    if not votes:
        return False
    counts = Counter(ip for _name, ip in votes)
    ranked = counts.most_common()
    top_count = ranked[0][1]
    second = ranked[1][1] if len(ranked) > 1 else 0
    return top_count > second + pending


def fetch_public_ip(
    proxy: str | None = None,
    timeout: float | None = None,
    *,
    total_timeout: float = DEFAULT_TOTAL_TIMEOUT,
    stagger: float = DEFAULT_STAGGER,
    per_request_cap: float = DEFAULT_PER_REQUEST_CAP,
    progress: ProgressCallback | None = None,
    logger: logging.Logger | None = None,
) -> tuple[str, str]:
    """Return (ipv4, provider_names). Raises if no consensus within the deadline.

    Providers start with a small stagger. Successful replies that arrive before
    ``total_timeout`` are majority-voted. A tie or zero replies means no public
    IP. Each attempt is capped (default 5s) with a wall-clock so DNS hangs still
    surface as errors instead of silent "no responses".
    """
    log = logger or logging.getLogger("chaddr")
    deadline_secs = float(timeout) if timeout is not None else float(total_timeout)
    providers = IPV4_PROVIDERS
    total = len(providers)
    if progress:
        progress(0, total)

    if total == 0:
        raise RuntimeError("no public-IP providers configured")

    cancel_event = threading.Event()
    started_at = time.monotonic()
    deadline_at = started_at + deadline_secs
    votes: list[tuple[str, str]] = []
    errors: list[str] = []
    finished = 0
    # Stop accepting successful votes after the deadline; still record errors.
    accepting = True
    lock = threading.Lock()

    def mark_finished() -> None:
        nonlocal finished
        with lock:
            finished += 1
            current = finished
        if progress:
            progress(min(current, total), total)

    def worker(name: str, url: str, delay: float) -> None:
        if delay > 0 and cancel_event.wait(delay):
            with lock:
                errors.append(f"{name}: cancelled before start")
            mark_finished()
            return
        if cancel_event.is_set():
            with lock:
                errors.append(f"{name}: cancelled before start")
            mark_finished()
            return
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            with lock:
                errors.append(f"{name}: deadline before start")
            mark_finished()
            return
        attempt_timeout = min(remaining, per_request_cap)
        try:
            provider, ip = _fetch_with_wall_clock(name, url, proxy, attempt_timeout)
            with lock:
                if accepting and time.monotonic() <= deadline_at:
                    votes.append((provider, ip))
                else:
                    errors.append(f"{provider}: late reply discarded ({ip})")
        except Exception as exc:
            with lock:
                errors.append(f"{name}: {exc}")
        finally:
            mark_finished()

    threads = [
        threading.Thread(
            target=worker,
            args=(name, url, index * stagger),
            name=f"public-ip-{name}",
            daemon=True,
        )
        for index, (name, url) in enumerate(providers)
    ]
    for thread in threads:
        thread.start()

    try:
        while True:
            now = time.monotonic()
            if now >= deadline_at:
                break
            with lock:
                snapshot = list(votes)
                done_n = finished
            pending = total - done_n
            if pending <= 0:
                break
            if _majority_is_decisive(snapshot, pending):
                break
            time.sleep(min(0.05, max(0.0, deadline_at - now)))
    finally:
        cancel_event.set()
        with lock:
            accepting = False

    # Brief grace so in-flight workers can record their timeout/error.
    grace_deadline = time.monotonic() + min(0.75, per_request_cap)
    while time.monotonic() < grace_deadline:
        with lock:
            if finished >= total:
                break
        time.sleep(0.05)

    if progress:
        progress(total, total)

    with lock:
        pending = total - finished
        if pending > 0:
            errors.append(
                f"{pending} provider(s) still pending after deadline "
                "(likely DNS or connect hang)"
            )
        vote_snapshot = list(votes)
        error_snapshot = list(errors)

    if vote_snapshot:
        tallies = ", ".join(
            f"{ip}×{count}" for ip, count in Counter(ip for _, ip in vote_snapshot).most_common()
        )
        log.debug(
            "Public IP votes (%d within %.1fs): %s",
            len(vote_snapshot),
            deadline_secs,
            tallies,
        )
    elif error_snapshot:
        log.warning(
            "Public IP probe got 0 replies within %.1fs; sample errors: %s",
            deadline_secs,
            "; ".join(error_snapshot[:5]),
        )

    picked = _pick_majority(vote_snapshot)
    if picked is None:
        if not vote_snapshot:
            detail = "; ".join(error_snapshot[:8]) or "no responses"
            raise RuntimeError(
                f"no public IP consensus (0 replies within {deadline_secs:g}s): {detail}"
            )
        tallies = ", ".join(
            f"{ip}×{count}" for ip, count in Counter(ip for _, ip in vote_snapshot).most_common()
        )
        raise RuntimeError(
            f"no public IP consensus (tie or ambiguous among {len(vote_snapshot)} replies: {tallies})"
        )
    return picked
