"""Load and parse address profiles from profile/<name> files."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

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


def resolve_profile_addresses(profile: Profile) -> "AddressSet":
    from chaddr.address import AddressSet, resolve_from

    if profile.from_block is None:
        return AddressSet()
    return resolve_from(profile.from_block.from_type, profile.from_block.options)
