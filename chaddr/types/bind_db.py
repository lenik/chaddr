"""BIND zone file (db) handler."""

from __future__ import annotations

from pathlib import Path

from chaddr.privilege import write_text as write_text_privileged
from chaddr.types.base import AddressTypeHandler, DiagnoseItem, DiagnoseResult, IPV4_RE, is_ipv4, is_ipv6


class BindDbHandler(AddressTypeHandler):
    type_name = "bind db"
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
            raise FileNotFoundError(f"bind db file not found: {path}")
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
                items.append(DiagnoseItem("A records", True, ", ".join(addresses)))
            else:
                items.append(
                    DiagnoseItem(
                        "A records",
                        False,
                        "no A record IPv4 addresses found",
                        "Ensure the zone file contains A records with IPv4 addresses.",
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

    def apply_manual(self, old_ip: str, new_ip: str) -> bool:
        if not is_ipv4(new_ip) and not is_ipv6(new_ip):
            raise ValueError(f"invalid IP address: {new_ip}")

        path = self._path()
        lines = self._read_lines()
        changed = False
        new_lines: list[str] = []
        record_field = self.config.get("record") or self.config.get("name")
        record_names = [n.strip() for n in record_field.split(",")] if record_field else None

        for line in lines:
            stripped = line.strip()
            if stripped.startswith(";") or not stripped:
                new_lines.append(line)
                continue
            if record_names:
                parts = stripped.split()
                if parts and parts[0].rstrip(".") in record_names and old_ip in line:
                    new_line = line.replace(old_ip, new_ip)
                    if new_line != line:
                        changed = True
                    new_lines.append(new_line)
                    continue
            if old_ip in line and not stripped.startswith(";"):
                new_line = line.replace(old_ip, new_ip)
                if new_line != line:
                    changed = True
                new_lines.append(new_line)
            else:
                new_lines.append(line)

        if not changed:
            self.logger.warning("No bind db entries matched old IP %s in %s", old_ip, path)
            return False

        write_text_privileged(
            path,
            "\n".join(new_lines) + ("\n" if new_lines else ""),
            encoding="utf-8",
        )
        return True
