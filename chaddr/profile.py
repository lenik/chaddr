"""Load and parse address profiles from profile/<name> files."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from chaddr.address import AddressSet, is_ipv4, is_ipv6
from chaddr.profile_lexer import (
    ArgRule,
    META_STARTER,
    TokenKind,
    arg_rule,
    canonical_ws_tokens,
    lex_line,
    logical_lines,
    parse_arg,
    tokenize_profile,
)
from chaddr.types import get_handler_class

try:
    from chaddr.buildconfig import PROFILE_DIR as _INSTALLED_PROFILE_DIR_STR
except ImportError:
    _INSTALLED_PROFILE_DIR_STR = "/usr/share/chaddr/profile"

PROFILE_DIR = Path(__file__).resolve().parent.parent / "profile"
USER_PROFILE_DIR = Path.home() / ".config" / "chaddr" / "profile"
_INSTALLED_PROFILE_DIR = Path(_INSTALLED_PROFILE_DIR_STR)
_active_profile_dir: Path | None = None

STARTER_FROM = "from"
STARTER_TYPE = "type"


def display_profile_path(path: Path) -> str:
    """Human-readable path with ~ for the home directory."""
    try:
        expanded = path.expanduser().resolve()
    except (OSError, RuntimeError):
        expanded = path.expanduser()
    home = Path.home()
    try:
        home_resolved = home.resolve()
        if expanded == home_resolved:
            return "~"
        relative = os.path.relpath(expanded, home_resolved)
        if not relative.startswith(".."):
            return f"~/{relative}".replace("\\", "/")
    except (OSError, RuntimeError, ValueError):
        pass
    return str(expanded).replace("\\", "/")


def format_profile_dir_label(path: Path | None = None) -> str:
    display = display_profile_path(path or get_profile_dir())
    if not display.endswith("/"):
        display += "/"
    return f"Profile: {display}"


def get_profile_dir() -> Path:
    if _active_profile_dir is not None:
        return _active_profile_dir
    env = os.environ.get("CHADDR_PROFILE_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    return USER_PROFILE_DIR


def ensure_profile_dir() -> Path:
    """Create the default profile directory and seed example profiles if empty."""
    root = get_profile_dir()
    root.mkdir(parents=True, exist_ok=True)
    if not any(item.is_file() and not item.name.startswith(".") for item in root.iterdir()):
        for source in (_INSTALLED_PROFILE_DIR, PROFILE_DIR):
            if not source.is_dir():
                continue
            for src in sorted(source.iterdir()):
                if not src.is_file() or src.name.startswith("."):
                    continue
                dest = root / src.name
                if not dest.exists():
                    shutil.copy2(src, dest)
    return root


def set_profile_dir(path: Path) -> None:
    global _active_profile_dir
    _active_profile_dir = path


@dataclass
class ProfileHeader:
    options: dict[str, str] = field(default_factory=dict)

    @property
    def description(self) -> str:
        return self.options.get("description", "")

    @property
    def version(self) -> str:
        return self.options.get("version", "")

    @property
    def addr_history(self) -> str:
        return self.options.get("addr-history", "")


def addr_history_to_sets(raw: str) -> list[AddressSet]:
    """Parse whitespace-separated historical addresses (IPv4/IPv6 auto-detected)."""
    sets: list[AddressSet] = []
    for part in raw.split():
        ip = part.strip()
        if not ip:
            continue
        if is_ipv4(ip):
            sets.append(AddressSet(ipv4=ip))
        elif is_ipv6(ip):
            sets.append(AddressSet(ipv6=ip))
    return sets


def _parse_addr_history_ips(raw: str) -> list[str]:
    ips: list[str] = []
    for part in raw.split():
        ip = part.strip()
        if ip and (is_ipv4(ip) or is_ipv6(ip)):
            ips.append(ip)
    return ips


def _header_boundary_line(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        token = lex_line(line, index + 1)
        if token and token.kind == TokenKind.ATTR and token.name in ("from", "type"):
            return index
    return len(lines)


def _addr_history_block_span(lines: list[str], header_end: int) -> tuple[int, int] | None:
    for index in range(header_end):
        token = lex_line(lines[index], index + 1)
        if token and token.kind == TokenKind.ATTR and token.name == "addr-history":
            end = index + 1
            while end < header_end and lines[end - 1].rstrip().endswith("\\"):
                end += 1
            return index, end
    return None


def append_profile_addr_history(profile_path: Path, ips: list[str]) -> bool:
    """Append unique IPs to profile header addr-history and save the file."""
    new_ips: list[str] = []
    for ip in ips:
        value = ip.strip()
        if value and (is_ipv4(value) or is_ipv6(value)) and value not in new_ips:
            new_ips.append(value)
    if not new_ips or not profile_path.is_file():
        return False

    lines = profile_path.read_text(encoding="utf-8").splitlines()
    header_end = _header_boundary_line(lines)
    span = _addr_history_block_span(lines, header_end)

    existing: list[str] = []
    if span is not None:
        block = "\n".join(lines[span[0] : span[1]])
        for logical in logical_lines(block):
            token = lex_line(logical, span[0] + 1)
            if token and token.kind == TokenKind.ATTR and token.name == "addr-history":
                existing = _parse_addr_history_ips(token.raw_arg or "")
                break

    merged = list(existing)
    seen = set(merged)
    changed = False
    for ip in new_ips:
        if ip not in seen:
            merged.append(ip)
            seen.add(ip)
            changed = True
    if not changed:
        return False

    new_line = f"addr-history: {' '.join(merged)}"
    if span is not None:
        lines[span[0] : span[1]] = [new_line]
    else:
        lines.insert(header_end, new_line)

    profile_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return True


@dataclass
class ProfileEntry:
    type: str
    options: dict[str, str] = field(default_factory=dict)
    cli_options: list[str] = field(default_factory=list)

    @property
    def config(self) -> dict[str, str]:
        return self.options


@dataclass
class Profile:
    name: str
    header: ProfileHeader | None = None
    from_block: ProfileFromBlock | None = None
    entries: list[ProfileEntry] = field(default_factory=list)
    path: Path | None = None
    global_options: list[str] = field(default_factory=list)

    @property
    def types(self) -> list[str]:
        return [e.type for e in self.entries]

    def has_manual_types(self) -> bool:
        for entry in self.entries:
            handler_cls = get_handler_class(entry.type)
            if handler_cls and handler_cls.supports_manual_edit:
                return True
        return False

    def allows_manual_edit(self) -> bool:
        for entry in self.entries:
            handler_cls = get_handler_class(entry.type)
            if handler_cls is None or not handler_cls.supports_manual_edit:
                return False
        return bool(self.entries)

    def addr_history_sets(self) -> list[AddressSet]:
        if self.header is None:
            return []
        return addr_history_to_sets(self.header.addr_history)


@dataclass
class ProfileFromBlock:
    from_type: str
    options: dict[str, str] = field(default_factory=dict)


def list_profiles(profile_dir: Path | None = None) -> list[str]:
    root = profile_dir or get_profile_dir()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_file() and not p.name.startswith("."))


def format_profile_label(name: str, header: ProfileHeader | None) -> str:
    if header is None or not header.options:
        return name
    desc = header.description.strip()
    ver = header.version.strip()
    extras: list[str] = []
    if desc:
        extras.append(desc)
    if ver:
        extras.append(ver if ver.lower().startswith("v") else f"v{ver}")
    if extras:
        return f"{name}  ·  {' · '.join(extras)}"
    return name


def list_profile_items(profile_dir: Path | None = None) -> list[tuple[str, str]]:
    """Return (profile name, display label) pairs for UI lists."""
    items: list[tuple[str, str]] = []
    for name in list_profiles(profile_dir):
        try:
            profile = load_profile(name, profile_dir)
            items.append((name, format_profile_label(name, profile.header)))
        except Exception:
            items.append((name, name))
    return items


def load_profile(name: str, profile_dir: Path | None = None) -> Profile:
    root = profile_dir or get_profile_dir()
    path = root / name
    if not path.is_file():
        raise FileNotFoundError(f"Profile not found: {path}")
    global_options: list[str] = []
    header: ProfileHeader | None = None
    from_block: ProfileFromBlock | None = None
    entries: list[ProfileEntry] = []
    for item in _parse_profile(path.read_text(encoding="utf-8"), global_options):
        if isinstance(item, ProfileHeader):
            header = item
        elif isinstance(item, ProfileFromBlock):
            from_block = item
        else:
            entries.append(item)
    return Profile(
        name=name,
        header=header,
        from_block=from_block,
        entries=entries,
        path=path,
        global_options=global_options,
    )


def merge_cli_options(profile: Profile, cli_options: dict[str, str]) -> dict[str, str]:
    merged = dict(cli_options)
    aliases = {"aws_ecret_access_key": "aws_secret_access_key"}
    for src, dst in aliases.items():
        if src in merged and dst not in merged:
            merged[dst] = merged[src]
    for entry in profile.entries:
        for opt in entry.cli_options:
            key = opt.lstrip("-").replace("-", "_")
            if key not in merged:
                merged[key] = ""
    for opt in profile.global_options:
        key = opt.lstrip("-").replace("-", "_")
        if key not in merged:
            merged[key] = ""
    return merged


def _parse_profile(
    text: str,
    global_options: list[str],
) -> Iterator[ProfileHeader | ProfileFromBlock | ProfileEntry]:
    current_header: ProfileHeader | None = None
    current_from: ProfileFromBlock | None = None
    current_entry: ProfileEntry | None = None
    pending_options: list[str] = []

    for token in tokenize_profile(text):
        if token.kind == TokenKind.OPTION:
            option = token.option or ""
            if current_entry is None and current_from is None and current_header is None:
                global_options.append(option.split()[0])
            pending_options.append(option)
            continue
        if token.kind != TokenKind.ATTR or token.name is None:
            continue

        key = token.name
        value = token.arg or ""

        if key == STARTER_FROM:
            if current_entry is not None:
                yield current_entry
                current_entry = None
            if current_header is not None:
                yield current_header
                current_header = None
            if current_from is not None:
                yield current_from
            current_from = ProfileFromBlock(from_type=value, options={})
            pending_options = []
            continue

        if key == STARTER_TYPE:
            if current_header is not None:
                yield current_header
                current_header = None
            if current_entry is not None:
                yield current_entry
            current_entry = ProfileEntry(type=value, cli_options=list(pending_options))
            pending_options = []
            continue

        if current_from is None and current_entry is None:
            if current_header is None:
                current_header = ProfileHeader(options={})
            current_header.options[key] = value
            continue

        if current_from is not None and current_entry is None:
            current_from.options[key] = value
            continue

        if current_entry is not None:
            current_entry.options[key] = value

    if current_entry is not None:
        yield current_entry
    if current_from is not None:
        yield current_from
    if current_header is not None:
        yield current_header


def update_profile_entry_option(
    profile_path: Path,
    entry_type: str,
    key: str,
    value: str,
    *,
    entry_index: int = 0,
) -> bool:
    """Set *key* on the *entry_index*'th profile block of *entry_type*."""
    if not profile_path.is_file():
        return False

    lines = profile_path.read_text(encoding="utf-8").splitlines()
    wanted = canonical_ws_tokens(entry_type).lower()
    blocks: list[tuple[int, int, str]] = []
    index = 0
    while index < len(lines):
        token = lex_line(lines[index], index + 1)
        if token and token.kind == TokenKind.ATTR and token.name == "type":
            block_type = parse_arg(
                token.raw_arg or "",
                arg_rule(META_STARTER, "type"),
            ).lower()
            start = index
            index += 1
            while index < len(lines):
                nxt = lex_line(lines[index], index + 1)
                if nxt and nxt.kind == TokenKind.ATTR and nxt.name in ("type", "from"):
                    break
                index += 1
            blocks.append((start, index, block_type))
        else:
            index += 1

    match = 0
    for start, end, block_type in blocks:
        if block_type != wanted:
            continue
        if match != entry_index:
            match += 1
            continue

        key_lower = key.strip().lower()
        for line_no in range(start + 1, end):
            token = lex_line(lines[line_no], line_no + 1)
            if token is None or token.kind != TokenKind.ATTR or token.name is None:
                continue
            if token.name == key_lower:
                prefix = lines[line_no].split(":", 1)[0]
                lines[line_no] = f"{prefix}: {value}"
                profile_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
                return True

        insert_at = start + 1
        while insert_at < end:
            stripped = lines[insert_at].strip()
            if stripped and not stripped.startswith("#"):
                if ":" in stripped:
                    insert_at += 1
                    continue
            insert_at += 1
        lines.insert(insert_at, f"{key}: {value}")
        profile_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return True

    return False


INSTANCE_FROM_HANDLERS: dict[str, str] = {
    "ec2 instance": "aws elastic ip",
    "aliyun instance": "aliyun elastic ip",
}


def is_instance_from_type(from_type: str) -> bool:
    return canonical_ws_tokens(from_type).lower() in INSTANCE_FROM_HANDLERS


def handler_config_for_instance_from(
    profile: Profile,
    from_block: ProfileFromBlock,
) -> tuple[str, dict[str, str]] | None:
    handler_type = INSTANCE_FROM_HANDLERS.get(canonical_ws_tokens(from_block.from_type).lower())
    if handler_type is None:
        return None
    config: dict[str, str] = {}
    for entry in profile.entries:
        if canonical_ws_tokens(entry.type).lower() == handler_type:
            config.update(entry.config)
    config.update(from_block.options)
    return handler_type, config


def instance_from_block_addresses(
    profile: Profile,
    from_block: ProfileFromBlock,
    cli_options: dict | None = None,
    proxy: str | None = None,
    logger: logging.Logger | None = None,
) -> AddressSet:
    from chaddr.types import create_handler

    spec = handler_config_for_instance_from(profile, from_block)
    if spec is None:
        return AddressSet()
    handler_type, config = spec
    log = logger or logging.getLogger("chaddr")
    merged_options = merge_cli_options(profile, cli_options or {})
    handler = create_handler(handler_type, config, merged_options, proxy, log)
    diag = handler.diagnose()
    if not diag.addresses:
        failed = next((item for item in diag.items if not item.ok), None)
        if failed:
            raise RuntimeError(f"{failed.label}: {failed.detail}")
    ipv4 = next((ip for ip in diag.addresses if is_ipv4(ip)), None)
    ipv6 = next((ip for ip in diag.addresses if is_ipv6(ip)), None)
    return AddressSet(ipv4=ipv4, ipv6=ipv6)


def instance_profile_addresses(
    profile: Profile,
    cli_options: dict | None = None,
    proxy: str | None = None,
    logger: logging.Logger | None = None,
) -> AddressSet:
    ipv4: str | None = None
    ipv6: str | None = None
    for from_block in profile_from_blocks(profile):
        if not is_instance_from_type(from_block.from_type):
            continue
        resolved = instance_from_block_addresses(profile, from_block, cli_options, proxy, logger)
        if resolved.ipv4:
            ipv4 = resolved.ipv4
        if resolved.ipv6:
            ipv6 = resolved.ipv6
    return AddressSet(ipv4=ipv4, ipv6=ipv6)


def resolve_profile_addresses(profile: Profile) -> "AddressSet":
    from chaddr.address import AddressSet, resolve_from

    ipv4: str | None = None
    ipv6: str | None = None
    for from_block in profile_from_blocks(profile):
        if canonical_ws_tokens(from_block.from_type).lower() != "resolve":
            continue
        resolved = resolve_from("resolve", from_block.options)
        if resolved.ipv4:
            ipv4 = resolved.ipv4
        if resolved.ipv6:
            ipv6 = resolved.ipv6
    return AddressSet(ipv4=ipv4, ipv6=ipv6)


def profile_from_blocks(profile: Profile) -> list[ProfileFromBlock]:
    """Return every from: block in profile file order."""
    if profile.path is None or not profile.path.is_file():
        return [profile.from_block] if profile.from_block is not None else []
    blocks: list[ProfileFromBlock] = []
    for item in _parse_profile(profile.path.read_text(encoding="utf-8"), []):
        if isinstance(item, ProfileFromBlock):
            blocks.append(item)
    return blocks


@dataclass(frozen=True)
class ProfileAddressFetchEvent:
    fraction: float
    message: str
    entries: list["AddressEntry"]
    replace_sources: frozenset[str] | None = None


def iter_profile_address_fetch(
    profile: Profile,
    cli_options: dict | None = None,
    proxy: str | None = None,
    logger: logging.Logger | None = None,
) -> Iterator[ProfileAddressFetchEvent]:
    """Yield address batches for progressive GUI loading (history → resolve → instance)."""
    from chaddr.address import AddressEntry, resolve_from

    log = logger or logging.getLogger("chaddr")
    options = cli_options or {}

    steps: list[tuple[str, frozenset[str] | None, Callable[[], list["AddressEntry"]]]] = []

    history: list[AddressEntry] = []
    for addr_set in profile.addr_history_sets():
        for ip in addr_set.all():
            if is_ipv4(ip) or is_ipv6(ip):
                history.append(AddressEntry.from_history_ip(ip))
    if history:
        steps.append(("Loading address history...", frozenset({"history"}), lambda h=history: h))

    old_ip = options.get("old_ip")
    if old_ip and (is_ipv4(str(old_ip)) or is_ipv6(str(old_ip))):
        steps.append(
            (
                "Loading old IP...",
                frozenset({"old-ip"}),
                lambda ip=str(old_ip): [AddressEntry.from_old_ip(ip)],
            )
        )

    resolve_blocks = [
        block
        for block in profile_from_blocks(profile)
        if canonical_ws_tokens(block.from_type).lower() == "resolve"
    ]
    for index, from_block in enumerate(resolve_blocks):
        hostname = (
            from_block.options.get("resolve")
            or from_block.options.get("host")
            or from_block.options.get("name")
            or "hostname"
        )

        def _resolve_entries(block=from_block) -> list[AddressEntry]:
            resolved = resolve_from("resolve", block.options)
            return AddressEntry.from_address_set(resolved, "resolve")

        steps.append(
            (
                f"Resolving {hostname}...",
                frozenset({"resolve"}) if index == 0 else None,
                _resolve_entries,
            )
        )

    instance_blocks = [
        block for block in profile_from_blocks(profile) if is_instance_from_type(block.from_type)
    ]
    for index, from_block in enumerate(instance_blocks):
        instance_id = (
            from_block.options.get("instance")
            or from_block.options.get("instance_id")
            or from_block.from_type
        )

        def _instance_entries(block=from_block) -> list[AddressEntry]:
            resolved = instance_from_block_addresses(profile, block, options, proxy, log)
            instance_id = block.options.get("instance") or block.options.get("instance_id") or ""
            entries: list[AddressEntry] = []
            if resolved.ipv4:
                entries.append(AddressEntry("IPv4", resolved.ipv4, "instance", detail=instance_id))
            if resolved.ipv6:
                entries.append(AddressEntry("IPv6", resolved.ipv6, "instance", detail=instance_id))
            return entries

        steps.append(
            (
                f"Fetching {instance_id}...",
                frozenset({"instance"}) if index == 0 else None,
                _instance_entries,
            )
        )

    if not steps:
        yield ProfileAddressFetchEvent(1.0, "No profile addresses", [], None)
        return

    total = len(steps)
    for index, (message, replace_sources, fetch) in enumerate(steps):
        try:
            entries = fetch()
        except Exception as exc:
            log.warning("Address fetch skipped for %s: %s", profile.name, exc)
            entries = []
        yield ProfileAddressFetchEvent(
            (index + 1) / total,
            message,
            entries,
            replace_sources,
        )


def list_profile_candidate_addresses(
    profile: Profile,
    cli_options: dict | None = None,
    proxy: str | None = None,
    logger: logging.Logger | None = None,
) -> list["AddressEntry"]:
    """Collect all candidate addresses for a profile with source labels."""
    from chaddr.address import AddressEntry

    entries: list[AddressEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for event in iter_profile_address_fetch(profile, cli_options, proxy, logger):
        if event.replace_sources:
            entries = [entry for entry in entries if entry.source not in event.replace_sources]
            seen = {(entry.family, entry.address, entry.source) for entry in entries}
        for entry in event.entries:
            key = (entry.family, entry.address, entry.source)
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
    return entries
