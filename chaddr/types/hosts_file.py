"""Hosts file handler."""

from __future__ import annotations

from pathlib import Path

from chaddr.address import SpareFromAddresses
from chaddr.privilege import write_text as write_text_privileged
from chaddr.types.base import AddressTypeHandler, DiagnoseItem, DiagnoseResult, is_ipv4, is_ipv6


APPLY_TARGETS_LABEL = "apply targets"


class HostsFileHandler(AddressTypeHandler):
    type_name = "hosts file"
    supports_manual_edit = True
    supports_reallocate = False

    def _path(self) -> Path:
        path = self.config.get("path", "").strip()
        if not path:
            raise RuntimeError("missing path in profile")
        return Path(path)

    def _read_lines(self) -> list[str]:
        path = self._path()
        if not path.is_file():
            raise FileNotFoundError(f"hosts file not found: {path}")
        return path.read_text(encoding="utf-8").splitlines()

    def _line_has_ip(self, line: str, ip: str, hosts: list[str] | None) -> bool:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return False
        parts = stripped.split()
        if not parts or parts[0] != ip:
            return False
        if not (is_ipv4(ip) or is_ipv6(ip)):
            return False
        if hosts:
            names = parts[1:]
            return any(name in hosts for name in names)
        return True

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

    def _find_from_addresses(self, lines: list[str]) -> tuple[list[str], list[DiagnoseItem]]:
        spare = self._effective_spare()
        if spare.is_empty():
            return [], []

        host_filter = self.config.get("host") or self.config.get("hosts")
        hosts = [h.strip() for h in host_filter.split(",")] if host_filter else None

        found: list[str] = []
        items: list[DiagnoseItem] = []
        for label, ips in (("IPv4", spare.ipv4), ("IPv6", spare.ipv6)):
            if not ips:
                continue
            matched = [ip for ip in ips if any(self._line_has_ip(line, ip, hosts) for line in lines)]
            if matched:
                found.extend(matched)
                detail = matched[0] if len(matched) == 1 else f"{matched[0]} (also: {', '.join(matched[1:])})"
                items.append(DiagnoseItem(label, True, f"found {detail}"))
            else:
                where = f" (host: {', '.join(hosts)})" if hosts else ""
                tried = ", ".join(ips)
                items.append(
                    DiagnoseItem(
                        label,
                        False,
                        f"from address not found in {self._path()}{where} (tried: {tried})",
                        "Ensure a spare from-source address appears in this hosts file.",
                    )
                )
        return found, items

    def _extract_ips(self, lines: list[str]) -> list[str]:
        host_filter = self.config.get("host") or self.config.get("hosts")
        hosts = [h.strip() for h in host_filter.split(",")] if host_filter else None
        ips: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if not parts or not is_ipv4(parts[0]):
                continue
            if hosts:
                names = parts[1:]
                if any(name in hosts for name in names):
                    ips.append(parts[0])
            else:
                ips.append(parts[0])
        return ips

    def _apply_target_lines(self, lines: list[str], old_ip: str | None = None) -> list[str]:
        """Lines that Apply would rewrite (shown in diagnostics)."""
        hosts_field = self.config.get("host") or self.config.get("hosts")
        hosts = [h.strip() for h in hosts_field.split(",")] if hosts_field else None

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
        matched: list[str] = []

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if not parts:
                continue
            addr = parts[0]
            if not (is_ipv4(addr) or is_ipv6(addr)):
                continue
            if hosts:
                names = parts[1:]
                if any(name in hosts for name in names):
                    matched.append(line)
                    continue
            if parts[0] in old_candidates:
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
                    'Add a line like "path: /etc/hosts" to the profile entry.',
                )
            )
            return DiagnoseResult(self.type_name, "path missing", False, items, addresses)

        try:
            lines = self._read_lines()
            items.append(DiagnoseItem("file", True, f"{len(lines)} lines"))

            if self._source_addresses and not self._source_addresses.is_empty():
                addresses, from_items = self._find_from_addresses(lines)
                items.extend(from_items)
            elif self._spare_from_addresses and not self._spare_from_addresses.is_empty():
                addresses, from_items = self._find_from_addresses(lines)
                items.extend(from_items)
            else:
                host_filter = self.config.get("host") or self.config.get("hosts")
                if not host_filter:
                    items.append(
                        DiagnoseItem(
                            "addresses",
                            False,
                            "no from-source address; add from: resolve to profile or host: filter",
                            'Add "host: name" to limit which entries are checked.',
                        )
                    )
                    ok = all(item.ok for item in items)
                    return DiagnoseResult(self.type_name, "issues found" if not ok else "ready", ok, items, addresses)
                addresses = sorted(set(self._extract_ips(lines)))
                if addresses:
                    detail = ", ".join(addresses)
                    ok_addrs = len(addresses) == 1
                    items.append(
                        DiagnoseItem(
                            "addresses",
                            ok_addrs,
                            detail,
                            "Multiple IPv4 entries for host filter."
                            if not ok_addrs
                            else "",
                        )
                    )
                else:
                    items.append(
                        DiagnoseItem(
                            "addresses",
                            False,
                            "no matching entries for host filter",
                            "Check host: names in the profile entry.",
                        )
                    )

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

        path = self._path()
        lines = self._read_lines()
        changed = False
        new_lines: list[str] = []
        hosts_field = self.config.get("host") or self.config.get("hosts")
        hosts = [h.strip() for h in hosts_field.split(",")] if hosts_field else None
        old_candidates = set(self._old_ip_candidates(old_ip))

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(line)
                continue
            parts = stripped.split()
            if not parts:
                new_lines.append(line)
                continue
            addr = parts[0]
            if not (is_ipv4(addr) or is_ipv6(addr)):
                new_lines.append(line)
                continue
            if hosts:
                names = parts[1:]
                if any(name in hosts for name in names):
                    parts[0] = new_ip
                    new_lines.append("\t".join(parts) if "\t" in line else " ".join(parts))
                    changed = True
                    continue
            if parts[0] in old_candidates:
                parts[0] = new_ip
                new_line = "\t".join(parts) if "\t" in line else " ".join(parts)
                new_lines.append(new_line)
                changed = True
            else:
                new_lines.append(line)

        if not changed:
            tried = ", ".join(sorted(old_candidates))
            self.logger.warning("No hosts entries matched old IP(s) [%s] in %s", tried, path)
            return False

        write_text_privileged(
            path,
            "\n".join(new_lines) + ("\n" if new_lines else ""),
            encoding="utf-8",
        )
        return True
