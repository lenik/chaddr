"""Profile listing, loading, parsing, and entry updates."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from chaddr.profile.history import (
    _HISTORY_ATTR_NAMES,
    parse_addr_history_arg,
    parse_history_addr_args,
)
from chaddr.profile.model import (
    Profile,
    ProfileEntry,
    ProfileFromBlock,
    ProfileHeader,
)
from chaddr.profile.paths import get_profile_dir
from chaddr.profile_lexer import (
    META_STARTER,
    TokenKind,
    arg_rule,
    canonical_ws_tokens,
    lex_line,
    parse_arg,
    tokenize_profile,
)

STARTER_FROM = "from"
STARTER_TYPE = "type"
# Type names that may appear as "name: /path" shorthand lines.
PATH_TYPE_SHORTHANDS = frozenset({"changelog", "file"})


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
            if current_from is not None:
                yield current_from
                current_from = None
            if current_entry is not None:
                yield current_entry
            current_entry = ProfileEntry(type=value, cli_options=list(pending_options))
            pending_options = []
            continue

        # Shorthand: "changelog: /path/to/file" (path-bearing type name as key).
        if key.lower() in PATH_TYPE_SHORTHANDS and value:
            if current_header is not None:
                yield current_header
                current_header = None
            if current_from is not None:
                yield current_from
                current_from = None
            if current_entry is not None:
                yield current_entry
            current_entry = ProfileEntry(
                type=key.lower(),
                options={"path": value},
                cli_options=list(pending_options),
            )
            pending_options = []
            continue

        if current_from is None and current_entry is None:
            if current_header is None:
                current_header = ProfileHeader(options={})
            if key in _HISTORY_ATTR_NAMES:
                if key == "history-addr":
                    current_header.addr_history_records.extend(parse_history_addr_args(token.args))
                else:
                    current_header.addr_history_records.extend(parse_addr_history_arg(value))
            else:
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


def profile_from_blocks(profile: Profile) -> list[ProfileFromBlock]:
    """Return every from: block in profile file order."""
    if profile.from_blocks is not None:
        return list(profile.from_blocks)
    if profile.path is None or not profile.path.is_file():
        return [profile.from_block] if profile.from_block is not None else []
    blocks: list[ProfileFromBlock] = []
    for item in _parse_profile(profile.path.read_text(encoding="utf-8"), []):
        if isinstance(item, ProfileFromBlock):
            blocks.append(item)
    return blocks
