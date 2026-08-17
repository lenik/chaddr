"""OpenWrt router handler — update Shadowsocks remote server via SSH/UCI."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from chaddr.address import is_ipv4, is_ipv6
from chaddr.types.base import (
    AddressTypeHandler,
    DiagnoseItem,
    DiagnoseResult,
)

_GATEWAY_HEX_RE = re.compile(r"^[0-9a-fA-F]{8}$")
_UCI_SERVER_SECTION_RE = re.compile(
    r"^(?P<pkg>[^.]+)\.(?P<section>[^=]+)=server\s*$",
)
_UCI_SERVER_VALUE_RE = re.compile(
    r"^(?P<pkg>[^.]+)\.(?P<section>[^.]+)\.server='?(?P<value>[^']*)'?\s*$",
)


def _option_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def find_default_gateway_ipv4() -> str | None:
    """Return the IPv4 default gateway from /proc/net/route, if any."""
    route = Path("/proc/net/route")
    if not route.is_file():
        return None
    try:
        lines = route.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        destination, gateway = parts[1], parts[2]
        if destination != "00000000" or not _GATEWAY_HEX_RE.match(gateway):
            continue
        raw = bytes.fromhex(gateway)
        return ".".join(str(b) for b in reversed(raw))
    return None


def _canonical_config_mode(raw: str) -> str:
    return " ".join(raw.strip().lower().split())


def _import_paramiko():
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError(
            "router type requires paramiko (install python3-paramiko)"
        ) from exc
    return paramiko


class RouterHandler(AddressTypeHandler):
    type_name = "router"
    supports_manual_edit = True
    supports_reallocate = False

    def _system(self) -> str:
        return (self.config.get("system") or "openwrt").strip().lower()

    def _config_mode(self) -> str:
        return _canonical_config_mode(self.config.get("config") or "")

    def _user(self) -> str:
        return (self.config.get("user") or "root").strip() or "root"

    def _password(self) -> str:
        return self.config.get("password") or self.options.get("router_password") or ""

    def _ssh_port(self) -> int:
        raw = (self.config.get("port") or self.config.get("ssh_port") or "22").strip()
        try:
            return int(raw)
        except ValueError as exc:
            raise RuntimeError(f"invalid SSH port: {raw}") from exc

    def _host(self) -> str:
        explicit = (
            self.config.get("host")
            or self.config.get("gateway")
            or self.config.get("router")
            or ""
        ).strip()
        if explicit:
            return explicit
        gateway = find_default_gateway_ipv4()
        if not gateway:
            raise RuntimeError("could not detect default gateway; set host: <router-ip>")
        return gateway

    def _is_optional(self) -> bool:
        return _option_truthy(self.config.get("optional"))

    def _connect(self):
        paramiko = _import_paramiko()
        password = self._password()
        if not password:
            raise RuntimeError('missing password: (or router_password in chaddr.conf)')
        host = self._host()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host,
            port=self._ssh_port(),
            username=self._user(),
            password=password,
            timeout=12,
            allow_agent=False,
            look_for_keys=False,
        )
        return client, host

    def _run(self, client, command: str, *, timeout: float = 30.0) -> tuple[int, str, str]:
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        return code, out, err

    def _old_ip_candidates(self, primary: str) -> list[str]:
        candidates = [primary]
        spare = self._apply_match_spare()
        pool = spare.ipv4 if is_ipv4(primary) else spare.ipv6 if is_ipv6(primary) else spare.all()
        for ip in pool:
            if ip not in candidates:
                candidates.append(ip)
        return candidates

    def _list_shadowsocks_servers(self, client) -> list[tuple[str, str, str]]:
        """Return (package, section, server_value) for UCI server sections."""
        code, out, err = self._run(
            client,
            "for pkg in shadowsocks-libev shadowsocks; do "
            "[ -f /etc/config/$pkg ] || continue; "
            "uci -q show \"$pkg\"; "
            "done",
        )
        if code != 0 and not out.strip():
            raise RuntimeError(f"uci show failed: {(err or out).strip() or f'exit {code}'}")

        server_sections: set[tuple[str, str]] = set()
        for line in out.splitlines():
            match = _UCI_SERVER_SECTION_RE.match(line.strip())
            if match:
                server_sections.add((match.group("pkg"), match.group("section")))

        found: list[tuple[str, str, str]] = []
        for line in out.splitlines():
            match = _UCI_SERVER_VALUE_RE.match(line.strip())
            if not match:
                continue
            pkg, section, value = match.group("pkg"), match.group("section"), match.group("value")
            if (pkg, section) not in server_sections:
                # Skip ss_redir.server references to section names.
                continue
            if value:
                found.append((pkg, section, value))
        return found

    def _restart_shadowsocks(self, client, packages: set[str]) -> None:
        for pkg in sorted(packages):
            self._run(
                client,
                f"if [ -x /etc/init.d/{shlex.quote(pkg)} ]; then "
                f"/etc/init.d/{shlex.quote(pkg)} restart; "
                f"elif [ -x /etc/init.d/shadowsocks-libev ]; then "
                f"/etc/init.d/shadowsocks-libev restart; "
                f"fi",
                timeout=60.0,
            )

    def diagnose(self) -> DiagnoseResult:
        items: list[DiagnoseItem] = []
        addresses: list[str] = []

        system = self._system()
        items.append(DiagnoseItem("system", True, system))
        if system != "openwrt":
            items.append(
                DiagnoseItem(
                    "system",
                    False,
                    f"unsupported system: {system}",
                    'Currently only "system: openwrt" is supported.',
                )
            )
            return DiagnoseResult(self.type_name, "issues found", False, items, addresses)

        mode = self._config_mode()
        items.append(DiagnoseItem("config", True, mode or "(missing)"))
        if mode != "shadowsocks remote server":
            items.append(
                DiagnoseItem(
                    "config",
                    False,
                    f"unsupported config: {mode or '(empty)'}",
                    'Use "config: shadowsocks remote server".',
                )
            )
            return DiagnoseResult(self.type_name, "issues found", False, items, addresses)

        if self._is_optional():
            items.append(DiagnoseItem("optional", True, "true (deselected by default)"))

        try:
            host = self._host()
            items.append(DiagnoseItem("router", True, host))
        except Exception as exc:
            items.append(
                DiagnoseItem(
                    "router",
                    False,
                    str(exc),
                    "Connect to the LAN or set host: <router-ip> in the profile.",
                )
            )
            return DiagnoseResult(self.type_name, "issues found", False, items, addresses)

        items.append(DiagnoseItem("user", True, self._user()))
        if not self._password():
            items.append(
                DiagnoseItem(
                    "password",
                    False,
                    "missing",
                    'Add "password: ..." to the profile or router_password in chaddr.conf.',
                )
            )
            return DiagnoseResult(self.type_name, "issues found", False, items, addresses)

        try:
            client, host = self._connect()
        except Exception as exc:
            items.append(
                DiagnoseItem(
                    "ssh",
                    False,
                    str(exc),
                    "Check host/user/password and that dropbear/sshd allows password login.",
                )
            )
            return DiagnoseResult(self.type_name, "issues found", False, items, addresses)

        try:
            code, uname, _err = self._run(client, "uname -a; [ -d /etc/config ] && echo OPENWRT_UCI=1")
            detail = uname.strip().splitlines()[0] if uname.strip() else f"exit {code}"
            items.append(DiagnoseItem("ssh", True, f"{host} ({detail})"))
            if "OPENWRT_UCI=1" not in uname:
                items.append(
                    DiagnoseItem(
                        "openwrt",
                        False,
                        "UCI config directory not found",
                        "Confirm the target is an OpenWrt router.",
                    )
                )
            else:
                items.append(DiagnoseItem("openwrt", True, "uci present"))

            servers = self._list_shadowsocks_servers(client)
            if not servers:
                items.append(
                    DiagnoseItem(
                        "shadowsocks",
                        False,
                        "no UCI server sections found",
                        "Install shadowsocks-libev and define a config server section.",
                    )
                )
            else:
                for pkg, section, value in servers:
                    addresses.append(value)
                    items.append(DiagnoseItem(f"{pkg}.{section}", True, value))
        except Exception as exc:
            items.append(DiagnoseItem("shadowsocks", False, str(exc)))
        finally:
            client.close()

        ok = all(item.ok for item in items)
        return DiagnoseResult(self.type_name, "ready" if ok else "issues found", ok, items, addresses)

    def apply_manual(self, old_ip: str, new_ip: str) -> bool:
        if not is_ipv4(new_ip) and not is_ipv6(new_ip):
            raise ValueError(f"invalid IP address: {new_ip}")
        if self._system() != "openwrt":
            raise RuntimeError(f"unsupported system: {self._system()}")
        if self._config_mode() != "shadowsocks remote server":
            raise RuntimeError(f"unsupported config: {self._config_mode()}")

        candidates = set(self._old_ip_candidates(old_ip))
        client, host = self._connect()
        try:
            servers = self._list_shadowsocks_servers(client)
            if not servers:
                self.logger.warning("No shadowsocks server sections on %s", host)
                return False

            changed_pkgs: set[str] = set()
            changed = 0
            for pkg, section, current in servers:
                if current == new_ip:
                    self.logger.info("  %s.%s.server already %s", pkg, section, new_ip)
                    continue
                if current not in candidates:
                    self.logger.info(
                        "  skip %s.%s.server=%s (not in old candidates)",
                        pkg,
                        section,
                        current,
                    )
                    continue
                cmd = (
                    f"uci set {shlex.quote(pkg)}.{shlex.quote(section)}.server={shlex.quote(new_ip)} "
                    f"&& uci commit {shlex.quote(pkg)}"
                )
                code, _out, err = self._run(client, cmd)
                if code != 0:
                    raise RuntimeError(
                        f"uci set {pkg}.{section}.server failed: {(err or '').strip() or code}"
                    )
                self.logger.info(
                    "  %s.%s.server: %s -> %s",
                    pkg,
                    section,
                    current,
                    new_ip,
                )
                changed_pkgs.add(pkg)
                changed += 1

            if not changed:
                self.logger.warning(
                    "No shadowsocks server values matched old IP(s) %s on %s",
                    ", ".join(sorted(candidates)),
                    host,
                )
                return False

            self._restart_shadowsocks(client, changed_pkgs)
            self.logger.info(
                "Updated %d shadowsocks server(s) on %s to %s",
                changed,
                host,
                new_ip,
            )
            return True
        finally:
            client.close()
