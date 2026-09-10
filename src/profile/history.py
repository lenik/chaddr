"""Address history records and profile history I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from chaddr.address import AddressSet, is_ipv4, is_ipv6
from chaddr.profile_lexer import TokenKind, lex_line, logical_lines, parse_shell_words

_HISTORY_ATTR_NAMES = frozenset({"addr-history", "history-addr"})

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")


@dataclass(frozen=True)
class AddrHistoryRecord:
    address: str
    insert_time: str = ""
    event_time: str = ""

    @property
    def timestamp(self) -> str:
        """Backward-compatible alias for insert_time."""
        return self.insert_time

    def format_line(self) -> str:
        """Emit canonical ``history-addr:`` line with double-quoted timestamps."""

        def quote(value: str) -> str:
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'

        parts = [f"history-addr: {self.address}"]
        if self.insert_time:
            parts.append(quote(self.insert_time))
            if self.event_time:
                parts.append(quote(self.event_time))
        elif self.event_time:
            parts.append('""')
            parts.append(quote(self.event_time))
        return " ".join(parts)


def format_addr_history_timestamp(when: datetime | None = None) -> str:
    return (when or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def parse_history_addr_args(args: Sequence[str] | tuple[str, ...] | list[str]) -> list[AddrHistoryRecord]:
    """Parse one ``history-addr:`` line from shell-split args.

    ``history-addr: <address> "<inserttime>" ["<eventtime>"]``
    """
    words = [part for part in args if part]
    if not words:
        return []
    address = words[0]
    if not (is_ipv4(address) or is_ipv6(address)):
        return []
    insert_time = words[1] if len(words) > 1 else ""
    event_time = words[2] if len(words) > 2 else ""
    return [AddrHistoryRecord(address, insert_time=insert_time, event_time=event_time)]


def parse_addr_history_arg(raw: str) -> list[AddrHistoryRecord]:
    """Parse legacy ``addr-history`` argument.

    Supports:
      - legacy: ``ip1 ip2 ip3``
      - stamped: ``ip YYYY-MM-DD HH:MM:SS``
      - stamped date-only: ``ip YYYY-MM-DD``
    """
    tokens = [part for part in (raw or "").split() if part]
    records: list[AddrHistoryRecord] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not (is_ipv4(token) or is_ipv6(token)):
            index += 1
            continue
        insert_time = ""
        if index + 1 < len(tokens) and _DATE_RE.match(tokens[index + 1]):
            if index + 2 < len(tokens) and _TIME_RE.match(tokens[index + 2]):
                insert_time = f"{tokens[index + 1]} {tokens[index + 2]}"
                index += 3
            else:
                insert_time = tokens[index + 1]
                index += 2
        else:
            index += 1
        records.append(AddrHistoryRecord(token, insert_time=insert_time))
    return records


def addr_history_to_sets(raw: str) -> list[AddressSet]:
    """Parse historical addresses from a raw addr-history argument."""
    sets: list[AddressSet] = []
    for record in parse_addr_history_arg(raw):
        ip = record.address
        if is_ipv4(ip):
            sets.append(AddressSet(ipv4=ip))
        elif is_ipv6(ip):
            sets.append(AddressSet(ipv6=ip))
    return sets


def addr_history_records_to_sets(records: list[AddrHistoryRecord]) -> list[AddressSet]:
    sets: list[AddressSet] = []
    for record in records:
        ip = record.address
        if is_ipv4(ip):
            sets.append(AddressSet(ipv4=ip))
        elif is_ipv6(ip):
            sets.append(AddressSet(ipv6=ip))
    return sets


def _header_boundary_line(lines: list[str]) -> int:
    from chaddr.profile.parse import PATH_TYPE_SHORTHANDS

    for index, line in enumerate(lines):
        token = lex_line(line, index + 1)
        if token and token.kind == TokenKind.ATTR and token.name in ("from", "type", *PATH_TYPE_SHORTHANDS):
            return index
    return len(lines)


def _read_addr_history_records(lines: list[str], header_end: int) -> list[AddrHistoryRecord]:
    records: list[AddrHistoryRecord] = []
    index = 0
    while index < header_end:
        token = lex_line(lines[index], index + 1)
        if token and token.kind == TokenKind.ATTR and token.name in _HISTORY_ATTR_NAMES:
            end = index + 1
            while end < header_end and lines[end - 1].rstrip().endswith("\\"):
                end += 1
            block = "\n".join(lines[index:end])
            for logical in logical_lines(block):
                item = lex_line(logical, index + 1)
                if not item or item.kind != TokenKind.ATTR or item.name not in _HISTORY_ATTR_NAMES:
                    continue
                raw = item.raw_arg or ""
                if item.name == "history-addr":
                    records.extend(parse_history_addr_args(parse_shell_words(raw)))
                else:
                    records.extend(parse_addr_history_arg(raw))
            index = end
            continue
        index += 1
    return records


def _strip_addr_history_lines(lines: list[str], header_end: int) -> tuple[list[str], int]:
    """Remove addr-history / history-addr lines from the header."""
    kept: list[str] = []
    removed = 0
    index = 0
    while index < header_end:
        token = lex_line(lines[index], index + 1)
        if token and token.kind == TokenKind.ATTR and token.name in _HISTORY_ATTR_NAMES:
            end = index + 1
            while end < header_end and lines[end - 1].rstrip().endswith("\\"):
                end += 1
            removed += end - index
            index = end
            continue
        kept.append(lines[index])
        index += 1
    kept.extend(lines[header_end:])
    return kept, header_end - removed


def write_profile_history(profile_path: Path, records: list[AddrHistoryRecord]) -> bool:
    """Replace header history with canonical ``history-addr:`` lines."""
    if not profile_path.is_file():
        return False

    lines = profile_path.read_text(encoding="utf-8").splitlines()
    header_end = _header_boundary_line(lines)
    lines, header_end = _strip_addr_history_lines(lines, header_end)

    ordered: list[AddrHistoryRecord] = []
    seen: set[str] = set()
    for record in records:
        address = record.address.strip()
        if not address or not (is_ipv4(address) or is_ipv6(address)):
            continue
        if address in seen:
            continue
        seen.add(address)
        ordered.append(
            AddrHistoryRecord(
                address,
                insert_time=(record.insert_time or "").strip(),
                event_time=(record.event_time or "").strip(),
            )
        )

    insert_at = header_end
    while insert_at > 0 and not lines[insert_at - 1].strip():
        insert_at -= 1
    history_lines = [record.format_line() for record in ordered]
    lines[insert_at:insert_at] = history_lines
    profile_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return True


def append_profile_addr_history(
    profile_path: Path,
    ips: list[str],
    *,
    when: datetime | None = None,
    event_time: str = "",
) -> bool:
    """Append unique IPs to profile history and rewrite as ``history-addr:`` lines.

    Always migrates legacy ``addr-history:`` lines to ``history-addr:`` on write.
    Returns True when the profile file was rewritten.
    """
    new_ips: list[str] = []
    for ip in ips:
        value = ip.strip()
        if value and (is_ipv4(value) or is_ipv6(value)) and value not in new_ips:
            new_ips.append(value)
    if not profile_path.is_file():
        return False

    lines = profile_path.read_text(encoding="utf-8").splitlines()
    header_end = _header_boundary_line(lines)
    existing = _read_addr_history_records(lines, header_end)
    by_addr = {record.address: record for record in existing}

    stamp = format_addr_history_timestamp(when)
    added = False
    for ip in new_ips:
        if ip in by_addr:
            continue
        by_addr[ip] = AddrHistoryRecord(ip, insert_time=stamp, event_time=event_time.strip())
        added = True

    ordered: list[AddrHistoryRecord] = []
    seen: set[str] = set()
    for record in existing:
        if record.address in seen:
            continue
        ordered.append(by_addr[record.address])
        seen.add(record.address)
    for ip in new_ips:
        if ip in seen:
            continue
        ordered.append(by_addr[ip])
        seen.add(ip)

    has_legacy = False
    for index in range(header_end):
        token = lex_line(lines[index], index + 1)
        if token and token.kind == TokenKind.ATTR and token.name == "addr-history":
            has_legacy = True
            break

    if not added and not has_legacy and ordered == existing:
        return False
    return write_profile_history(profile_path, ordered)
