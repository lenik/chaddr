"""Plain-text file handler — replace IP literals with alnum boundary matching."""

from __future__ import annotations

import re
from pathlib import Path

from chaddr.address import SpareFromAddresses
from chaddr.privilege import write_text as write_text_privileged
from chaddr.types.base import (
    AddressTypeHandler,
    DiagnoseItem,
    DiagnoseResult,
    is_ipv4,
    is_ipv6,
)
from chaddr.types.hosts_file import APPLY_TARGETS_LABEL

_ALNUM_BEFORE = r"(?<![0-9A-Za-z])"
_ALNUM_AFTER = r"(?![0-9A-Za-z])"


def _ip_pattern(ip: str) -> re.Pattern[str]:
    return re.compile(_ALNUM_BEFORE + re.escape(ip) + _ALNUM_AFTER)


def _line_has_ip(line: str, ip: str) -> bool:
    if is_ipv4(ip):
        return bool(_ip_pattern(ip).search(line))
    if is_ipv6(ip):
        return bool(_ip_pattern(ip).search(line))
    return False


def _replace_ip_in_text(text: str, old_ip: str, new_ip: str) -> tuple[str, int]:
    pattern = _ip_pattern(old_ip)
    new_text, count = pattern.subn(new_ip, text)
    return new_text, count


class PlainFileHandler(AddressTypeHandler):
    type_name = "file"
    supports_manual_edit = True
    supports_reallocate = False

    def _path(self) -> Path:
        path = self.config.get("path", "").strip()
        if not path:
            raise RuntimeError("missing path in profile")
        return Path(path)

    def _read_text(self) -> str:
        path = self._path()
        if not path.is_file():
            raise FileNotFoundError(f"file not found: {path}")
        return path.read_text(encoding="utf-8")

    def _read_lines(self) -> list[str]:
        text = self._read_text()
        if not text:
            return []
        lines = text.splitlines()
        if text.endswith("\n"):
            return lines
        return lines

    def _effective_spare(self) -> SpareFromAddresses:
        if self._spare_from_addresses and not self._spare_from_addresses.is_empty():
            return self._spare_from_addresses
        if self._source_addresses and not self._source_addresses.is_empty():
            return SpareFromAddresses.from_address_sets(self._source_addresses)
        return SpareFromAddresses()

    def _old_ip_candidates(self, primary: str, spare: SpareFromAddresses | None = None) -> list[str]:
        candidates = [primary]
        pool_spare = spare if spare is not None else self._effective_spare()
        if is_ipv4(primary):
            pool = pool_spare.ipv4
        elif is_ipv6(primary):
            pool = pool_spare.ipv6
        else:
            pool = pool_spare.all()
        for ip in pool:
            if ip not in candidates:
                candidates.append(ip)
        return candidates

    def _find_from_addresses(self, text: str) -> tuple[list[str], list[DiagnoseItem]]:
        spare = self._effective_spare()
        if spare.is_empty():
            return [], []

        found: list[str] = []
        items: list[DiagnoseItem] = []
        for label, ips in (("IPv4", spare.ipv4), ("IPv6", spare.ipv6)):
            if not ips:
                continue
            matched = [ip for ip in ips if _line_has_ip(text, ip)]
            if matched:
                found.extend(matched)
                detail = matched[0] if len(matched) == 1 else f"{matched[0]} (also: {', '.join(matched[1:])})"
                items.append(DiagnoseItem(label, True, f"found {detail}"))
            else:
                tried = ", ".join(ips)
                items.append(
                    DiagnoseItem(
                        label,
                        False,
                        f"from address not found in {self._path()} (tried: {tried})",
                        "Ensure a spare from-source address appears in this file.",
                    )
                )
        return found, items

    def _apply_target_lines(self, lines: list[str], old_ip: str | None = None) -> list[str]:
        primary = (old_ip or "").strip()
        if not primary:
            match_spare = self._apply_match_spare()
            if match_spare.ipv4:
                primary = match_spare.ipv4[0]
            elif match_spare.ipv6:
                primary = match_spare.ipv6[0]
            elif self._source_addresses and not self._source_addresses.is_empty():
                primary = self._source_addresses.ipv4 or self._source_addresses.ipv6 or ""

        old_candidates = (
            set(self._old_ip_candidates(primary, self._apply_match_spare())) if primary else set()
        )
        if not old_candidates:
            return []

        matched: list[str] = []
        for line in lines:
            if any(_line_has_ip(line, ip) for ip in old_candidates):
                matched.append(line)
        return matched

    def diagnose(self) -> DiagnoseResult:
        items: list[DiagnoseItem] = []
        addresses: list[str] = []

        try:
            path = self._path()
            items.append(DiagnoseItem("path", True, str(path)))
        except Exception as exc:
            items.append(
                DiagnoseItem(
                    "path",
                    False,
                    str(exc),
                    'Add a line like "path: /path/to/file" to the profile entry.',
                )
            )
            return DiagnoseResult(self.type_name, "path missing", False, items, addresses)

        try:
            text = self._read_text()
            lines = text.splitlines()
            items.append(DiagnoseItem("file", True, f"{len(lines)} lines"))

            if self._source_addresses and not self._source_addresses.is_empty():
                addresses, from_items = self._find_from_addresses(text)
                items.extend(from_items)
            elif self._spare_from_addresses and not self._spare_from_addresses.is_empty():
                addresses, from_items = self._find_from_addresses(text)
                items.extend(from_items)
            else:
                items.append(
                    DiagnoseItem(
                        "addresses",
                        False,
                        "no from-source address; add from: resolve to profile",
                        "Add from: resolve so spare addresses can be matched in this file.",
                    )
                )
                ok = all(item.ok for item in items)
                return DiagnoseResult(self.type_name, "issues found" if not ok else "ready", ok, items, addresses)

            target_lines = self._apply_target_lines(lines)
            if target_lines:
                items.append(
                    DiagnoseItem(
                        APPLY_TARGETS_LABEL,
                        True,
                        "\n".join(target_lines),
                    )
                )
        except FileNotFoundError:
            items.append(
                DiagnoseItem(
                    "file",
                    False,
                    f"file not found: {path}",
                    "Create the file or fix the path in the profile.",
                )
            )
        except PermissionError:
            items.append(
                DiagnoseItem(
                    "file",
                    False,
                    f"permission denied: {path}",
                    "Run with sufficient privileges, approve the GUI elevation prompt, or adjust file permissions.",
                )
            )
        except Exception as exc:
            items.append(DiagnoseItem("file", False, str(exc), "Check file readability."))

        ok = all(item.ok for item in items)
        return DiagnoseResult(self.type_name, "ready" if ok else "issues found", ok, items, addresses)

    def apply_manual(self, old_ip: str, new_ip: str) -> bool:
        if not is_ipv4(new_ip) and not is_ipv6(new_ip):
            raise ValueError(f"invalid IP address: {new_ip}")
        if is_ipv4(old_ip) and not is_ipv4(new_ip):
            raise ValueError(f"IPv4 replacement requires IPv4 new address, got: {new_ip}")
        if is_ipv6(old_ip) and not is_ipv6(new_ip):
            raise ValueError(f"IPv6 replacement requires IPv6 new address, got: {new_ip}")

        path = self._path()
        text = self._read_text()
        old_candidates = sorted(
            self._old_ip_candidates(old_ip, self._apply_match_spare()),
            key=len,
            reverse=True,
        )
        self.logger.info(
            "file %s: replace %s -> %s, candidates [%s]",
            path,
            old_ip,
            new_ip,
            ", ".join(old_candidates),
        )

        changed = False
        total_count = 0
        new_text = text
        for candidate in old_candidates:
            if is_ipv4(old_ip) and not is_ipv4(candidate):
                continue
            if is_ipv6(old_ip) and not is_ipv6(candidate):
                continue
            new_text, count = _replace_ip_in_text(new_text, candidate, new_ip)
            if count:
                self.logger.info("  replaced %s: %d occurrence(s)", candidate, count)
                total_count += count
                changed = True

        if not changed:
            tried = ", ".join(old_candidates)
            self.logger.warning("No matches for old IP(s) [%s] in %s", tried, path)
            return False

        if text.endswith("\n") and not new_text.endswith("\n"):
            new_text += "\n"
        elif not text.endswith("\n") and new_text.endswith("\n"):
            new_text = new_text.rstrip("\n")

        write_text_privileged(path, new_text, encoding="utf-8")
        self.logger.info("Updated %s: %d replacement(s) %s -> %s", path, total_count, old_ip, new_ip)
        return True
