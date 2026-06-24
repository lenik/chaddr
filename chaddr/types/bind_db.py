"""BIND zone file (db) handler."""

from __future__ import annotations

from pathlib import Path

from chaddr.address import SpareFromAddresses
from chaddr.privilege import write_text as write_text_privileged
from chaddr.types.base import AddressTypeHandler, DiagnoseItem, DiagnoseResult, IPV4_RE, is_ipv4, is_ipv6
from chaddr.types.hosts_file import APPLY_TARGETS_LABEL

BIND_ZONE_A_IPS_LABEL = "unique A record IPs in zone file"


class BindDbHandler(AddressTypeHandler):
    type_name = "zone file"
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
            raise FileNotFoundError(f"zone file not found: {path}")
        return path.read_text(encoding="utf-8").splitlines()

    def _extract_a_record_ips(self, lines: list[str]) -> list[str]:
        ips: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith(";") or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) >= 5 and parts[3].upper() == "A" and is_ipv4(parts[4]):
                ips.append(parts[4])
            elif len(parts) >= 4 and parts[2].upper() == "A" and is_ipv4(parts[3]):
                ips.append(parts[3])
            else:
                for match in IPV4_RE.findall(stripped):
                    if "SOA" not in stripped.upper():
                        ips.append(match)
        return ips

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

    def _apply_target_lines(self, lines: list[str], old_ip: str | None = None) -> list[str]:
        """Lines that Apply would rewrite (shown in diagnostics)."""
        record_field = self.config.get("record") or self.config.get("name")
        record_names = [n.strip() for n in record_field.split(",")] if record_field else None

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
            stripped = line.strip()
            if not stripped or stripped.startswith(";"):
                continue
            if record_names:
                parts = stripped.split()
                if parts and parts[0].rstrip(".") in record_names:
                    if any(ip in line for ip in old_candidates):
                        matched.append(line)
                continue
            if any(ip in line for ip in old_candidates):
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
                    'Add a line like "path: /path/to/zone.db" to the profile entry.',
                )
            )
            return DiagnoseResult(self.type_name, "path missing", False, items, addresses)

        try:
            lines = self._read_lines()
            items.append(DiagnoseItem("file", True, f"{len(lines)} lines"))
            addresses = sorted(set(self._extract_a_record_ips(lines)))
            if addresses:
                items.append(DiagnoseItem(BIND_ZONE_A_IPS_LABEL, True, ", ".join(addresses)))
            else:
                items.append(
                    DiagnoseItem(
                        BIND_ZONE_A_IPS_LABEL,
                        False,
                        "no A record IPv4 addresses found",
                        "Ensure the zone file contains A records with IPv4 addresses.",
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
                    "Create the zone file or fix the path in the profile.",
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

    def _replace_a_record_ip(self, line: str, parts: list[str], new_ip: str) -> str:
        for idx in (4, 3):
            if len(parts) > idx and parts[idx - 1].upper() == "A" and is_ipv4(parts[idx]):
                return line.replace(parts[idx], new_ip, 1)
        for candidate in sorted({p for p in parts if is_ipv4(p)}, key=len, reverse=True):
            if candidate in line:
                return line.replace(candidate, new_ip, 1)
        return line

    def apply_manual(self, old_ip: str, new_ip: str) -> bool:
        if not is_ipv4(new_ip) and not is_ipv6(new_ip):
            raise ValueError(f"invalid IP address: {new_ip}")

        path = self._path()
        lines = self._read_lines()
        changed = False
        match_count = 0
        new_lines: list[str] = []
        record_field = self.config.get("record") or self.config.get("name")
        record_names = [n.strip() for n in record_field.split(",")] if record_field else None
        old_candidates = set(self._old_ip_candidates(old_ip, self._apply_match_spare()))
        self.logger.info(
            "zone file %s: replace %s -> %s, candidates [%s], record filter %s",
            path,
            old_ip,
            new_ip,
            ", ".join(sorted(old_candidates)),
            ", ".join(record_names) if record_names else "(any)",
        )

        for line in lines:
            stripped = line.strip()
            if stripped.startswith(";") or not stripped:
                new_lines.append(line)
                continue
            parts = stripped.split()
            if record_names:
                if parts and parts[0].rstrip(".") in record_names:
                    new_line = self._replace_a_record_ip(line, parts, new_ip)
                    if new_line != line:
                        changed = True
                        match_count += 1
                    new_lines.append(new_line)
                    continue
            new_line = line
            line_changed = False
            for candidate in sorted(old_candidates, key=len, reverse=True):
                if candidate in new_line:
                    new_line = new_line.replace(candidate, new_ip)
                    line_changed = True
            if line_changed:
                changed = True
                match_count += 1
                new_lines.append(new_line)
            else:
                new_lines.append(line)

        if not changed:
            tried = ", ".join(sorted(old_candidates))
            self.logger.warning("No zone file entries matched old IP(s) [%s] in %s", tried, path)
            return False

        write_text_privileged(
            path,
            "\n".join(new_lines) + ("\n" if new_lines else ""),
            encoding="utf-8",
        )
        self.logger.info("Updated %s: %d line(s) %s -> %s", path, match_count, old_ip, new_ip)
        return True
