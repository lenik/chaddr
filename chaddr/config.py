"""Load JSON configuration (API keys, secrets, proxy)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "chaddr.conf"

try:
    from chaddr.buildconfig import DOC_DIR as _DOC_DIR_STR
except ImportError:
    _DOC_DIR_STR = ""

CONFIG_SEARCH_DIRS: tuple[Path, ...] = (
    Path.cwd(),
    Path.home() / ".config" / "chaddr",
)
CONFIG_EXAMPLE_DIR = Path(_DOC_DIR_STR) if _DOC_DIR_STR else None
CLIENT_IP_TTL = timedelta(hours=1)


def _normalize_key(key: str) -> str:
    return key.lstrip("-").replace("-", "_")


def _flatten_config(data: dict[str, Any]) -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in data.items():
        if key.startswith("_"):
            continue
        if value is None:
            continue
        if isinstance(value, bool):
            flat[_normalize_key(key)] = "1" if value else ""
        elif isinstance(value, (int, float)):
            flat[_normalize_key(key)] = str(value)
        elif isinstance(value, str):
            flat[_normalize_key(key)] = value
        else:
            raise ValueError(f"unsupported config value for {key!r}: {type(value).__name__}")
    aliases = {"aws_ecret_access_key": "aws_secret_access_key"}
    for src, dst in aliases.items():
        if src in flat and dst not in flat:
            flat[dst] = flat[src]
    return flat


def resolve_config_path(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"config file not found: {path}")
        return path

    for directory in CONFIG_SEARCH_DIRS:
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(explicit: str | None = None) -> tuple[dict[str, str], str | None, Path | None]:
    """Return (options, proxy, path). CLI flags should override returned options."""
    path = resolve_config_path(explicit)
    if path is None:
        return {}, None, None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"config root must be a JSON object: {path}")

    options = _flatten_config(raw)
    proxy = options.pop("proxy", None) or None
    if proxy == "":
        proxy = None
    return options, proxy, path


def load_config_raw(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"config root must be a JSON object: {path}")
    return raw


def save_config(path: Path, updates: dict[str, Any], merge: bool = True) -> None:
    data: dict[str, Any] = {}
    if merge and path.is_file():
        data = load_config_raw(path)
    for key, value in updates.items():
        if value is None or value == "":
            data.pop(key, None)
        else:
            data[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def format_client_ip_expire(moment: datetime | None = None) -> str:
    when = moment or (datetime.now(timezone.utc) + CLIENT_IP_TTL)
    return when.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_client_ip_expire(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def cached_client_ip(options: dict[str, str]) -> str | None:
    ip = (options.get("client_ip") or "").strip()
    if not ip:
        return None
    expire_raw = options.get("client_ip_expire")
    if not expire_raw or not str(expire_raw).strip():
        return ip
    expire = parse_client_ip_expire(str(expire_raw))
    if expire is None:
        return ip
    if datetime.now(timezone.utc) < expire:
        return ip
    return None


def persist_client_ip(config_path: Path, ip: str) -> None:
    save_config(
        config_path,
        {
            "client_ip": ip,
            "client_ip_expire": format_client_ip_expire(datetime.now(timezone.utc) + CLIENT_IP_TTL),
        },
    )


def resolve_client_ip(
    options: dict[str, str],
    proxy: str | None,
    config_path: Path | None,
    logger: logging.Logger | None = None,
) -> tuple[str | None, str]:
    """Use cached client IP when valid, otherwise fetch, update options, and persist."""
    log = logger or logging.getLogger("chaddr")

    cached = cached_client_ip(options)
    if cached:
        options["client_ip"] = cached
        expire = options.get("client_ip_expire")
        if expire:
            log.info("Using cached client IP: %s (expires %s)", cached, expire)
        else:
            log.info("Using configured client IP: %s", cached)
        return cached, "cache"

    try:
        from chaddr.public_ip import fetch_public_ip
    except ImportError as exc:
        log.warning("Could not import public IP helper: %s", exc)
        return None, ""

    try:
        ip, source = fetch_public_ip(proxy)
    except Exception as exc:
        log.warning("Could not fetch public IP: %s", exc)
        return None, ""

    options["client_ip"] = ip
    options["client_ip_expire"] = format_client_ip_expire(datetime.now(timezone.utc) + CLIENT_IP_TTL)

    if config_path:
        try:
            persist_client_ip(config_path, ip)
            log.info("Saved client IP %s to %s (expires in 1h)", ip, config_path)
        except OSError as exc:
            log.warning("Could not save client IP to config: %s", exc)

    log.info("Public IP (%s): %s", source, ip)
    return ip, source

