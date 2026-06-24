"""Registered nameserver handler (Namecheap glue records)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import requests

from chaddr.namecheap_portal import ensure_client_ip_whitelisted, is_portal_whitelist_enabled
from chaddr.proxy import requests_proxies
from chaddr.types.base import AddressTypeHandler, DiagnoseItem, DiagnoseResult, AddressSet, is_ipv4


class RegisteredNameserverHandler(AddressTypeHandler):
    type_name = "registered nameserver"
    supports_manual_edit = True
    supports_reallocate = False

    def _ns_hosts(self) -> list[str]:
        raw = self.config.get("ns", "")
        return [part.strip() for part in raw.split(",") if part.strip()]

    def _ns_domain_parts(self, host: str) -> tuple[str, str]:
        domain = self.config.get("domain") or self.config.get("ns_domain")
        if domain:
            labels = domain.strip(".").split(".")
            if len(labels) < 2:
                raise RuntimeError(f"invalid domain in profile: {domain!r}")
            return labels[0], ".".join(labels[1:])

        labels = host.strip(".").split(".")
        if len(labels) < 3:
            raise RuntimeError(
                f"cannot derive domain from nameserver {host!r}; add domain: example.com to profile"
            )
        parent_labels = labels[1:]
        return parent_labels[0], ".".join(parent_labels[1:])

    def _namecheap_params(self) -> dict[str, str]:
        api_key = self.options.get("namecheap_api_key")
        username = self.options.get("namecheap_username")
        client_ip = self.options.get("client_ip")
        missing = []
        if not api_key:
            missing.append("namecheap_api_key")
        if not username:
            missing.append("namecheap_username")
        if not client_ip:
            missing.append("client_ip")
        if missing:
            raise RuntimeError(f"missing options: {', '.join(missing)}")
        return {
            "ApiUser": username,
            "ApiKey": api_key,
            "UserName": username,
            "ClientIp": client_ip,
        }

    def _namecheap_api_url(self) -> str:
        sandbox = self.options.get("namecheap_sandbox", "").lower() in ("1", "true", "yes")
        if sandbox:
            return "https://api.sandbox.namecheap.com/xml.response"
        return "https://api.namecheap.com/xml.response"

    def _is_client_ip_api_error(self, message: str) -> bool:
        lowered = message.lower()
        markers = (
            "clientip",
            "client ip",
            "requestip",
            "request ip",
            "whitelist",
            "1011150",
            "1017105",
            "1017150",
        )
        return any(marker in lowered for marker in markers)

    def _portal_whitelist_enabled(self) -> bool:
        return is_portal_whitelist_enabled(self.options)

    def _refresh_whitelist_via_portal(self) -> None:
        password = self.options.get("namecheap_password")
        username = self.options.get("namecheap_username")
        client_ip = self.options.get("client_ip")
        if not password or not username or not client_ip:
            raise RuntimeError(
                "Namecheap API requires whitelisted client IP; set namecheap_password to update via portal"
            )
        sandbox = self.options.get("namecheap_sandbox", "").lower() in ("1", "true", "yes")
        entry_name = self.options.get("namecheap_whitelist_name") or None
        ensure_client_ip_whitelisted(
            username,
            password,
            client_ip,
            proxy=self.proxy,
            sandbox=sandbox,
            entry_name=entry_name,
            logger=self.logger,
        )

    def _namecheap_request(self, command: str, extra: dict[str, str]) -> ET.Element:
        params = self._namecheap_params()
        params["Command"] = command
        params.update(extra)
        url = self._namecheap_api_url()

        def call_api() -> ET.Element:
            response = requests.get(
                url,
                params=params,
                timeout=60,
                proxies=requests_proxies(self.proxy),
            )
            response.raise_for_status()
            root = ET.fromstring(response.text)
            status = root.attrib.get("Status")
            if status != "OK":
                errors = [err.text or "" for err in root.findall(".//{*}Errors/{*}Error")]
                raise RuntimeError("; ".join(errors) or "Namecheap API error")
            return root

        try:
            return call_api()
        except RuntimeError as exc:
            if self._is_client_ip_api_error(str(exc)) and self._portal_whitelist_enabled():
                self.logger.info("Namecheap API rejected client IP; refreshing whitelist via portal")
                self._refresh_whitelist_via_portal()
                return call_api()
            raise

    def _parse_ns_ip(self, root: ET.Element) -> str | None:
        node = root.find(".//{*}DomainNSInfoResult")
        if node is not None:
            ip = node.attrib.get("IP")
            if ip:
                return ip.strip()
        ip = root.findtext(".//{*}DomainNSInfoResult/@IP")
        return ip.strip() if ip else None

    def _get_namecheap_ips(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for host in self._ns_hosts():
            sld, tld = self._ns_domain_parts(host)
            root = self._namecheap_request(
                "namecheap.domains.ns.getInfo",
                {"SLD": sld, "TLD": tld, "Nameserver": host},
            )
            ip = self._parse_ns_ip(root)
            if ip:
                result[host] = ip
        return result

    def _set_namecheap_ip(self, host: str, ip: str, *, current: str | None = None) -> None:
        sld, tld = self._ns_domain_parts(host)
        if current is None:
            current = self._get_namecheap_ips().get(host)
        params: dict[str, str] = {
            "SLD": sld,
            "TLD": tld,
            "Nameserver": host,
            "IP": ip,
        }
        if current:
            params["OldIP"] = current
        self._namecheap_request("namecheap.domains.ns.update", params)

    def diagnose(self) -> DiagnoseResult:
        items: list[DiagnoseItem] = []
        addresses: list[str] = []
        api = self.config.get("api", "").lower()
        ns_hosts = self._ns_hosts()

        if not api:
            items.append(
                DiagnoseItem("api", False, "missing api field", 'Add a line like "api: namecheap".')
            )
            return DiagnoseResult(self.type_name, "api not configured", False, items, addresses)

        items.append(DiagnoseItem("api", True, api))

        if not ns_hosts:
            items.append(
                DiagnoseItem(
                    "nameservers",
                    False,
                    "no nameservers configured",
                    'Add a line like "ns: ns1.example.com, ns2.example.com".',
                )
            )
            return DiagnoseResult(self.type_name, "nameservers missing", False, items, addresses)

        items.append(DiagnoseItem("nameservers", True, ", ".join(ns_hosts)))

        if api != "namecheap":
            items.append(
                DiagnoseItem(
                    "api support",
                    False,
                    f"unsupported api: {api}",
                    "Currently only namecheap is implemented.",
                )
            )
            return DiagnoseResult(self.type_name, "unsupported api", False, items, addresses)

        try:
            mapping = self._get_namecheap_ips()
            for host, ip in mapping.items():
                if is_ipv4(ip):
                    addresses.append(ip)
                items.append(DiagnoseItem(f"ns {host}", True, ip))
            if not mapping:
                items.append(
                    DiagnoseItem(
                        "nameserver records",
                        False,
                        "no IP addresses returned",
                        "Verify nameserver hostnames are registered in Namecheap.",
                    )
                )
        except Exception as exc:
            guidance = "Check API key, username, client IP whitelist, and proxy settings."
            if self._portal_whitelist_enabled():
                guidance += (
                    " Set namecheap_password and namecheap_whitelist to portal"
                    " to update whitelist via portal on failure."
                )
            items.append(
                DiagnoseItem(
                    "namecheap api",
                    False,
                    str(exc),
                    guidance,
                )
            )

        unique = sorted(set(addresses))
        if len(unique) > 1:
            items.append(
                DiagnoseItem(
                    "consistency",
                    False,
                    f"multiple IPs found: {', '.join(unique)}",
                    "All nameservers in the profile should point to the same address.",
                )
            )
        elif unique:
            items.append(DiagnoseItem("consistency", True, unique[0]))

        ok = all(item.ok for item in items)
        return DiagnoseResult(self.type_name, "ready" if ok else "issues found", ok, items, unique)

    def apply_address_map(self, old: AddressSet, new: AddressSet) -> bool:
        """Update configured glue NS to *new* IPv4; ignore profile old-address map."""
        if not new.ipv4:
            self.logger.warning("[%s] skip: no new IPv4 selected", self.type_name)
            return False
        ns_hosts = self._ns_hosts()
        if not ns_hosts:
            self.logger.warning("[%s] skip: no nameservers configured", self.type_name)
            return False
        self.logger.info(
            "[%s] apply nameservers %s -> %s",
            self.type_name,
            ", ".join(ns_hosts),
            new.ipv4,
        )
        if not old.is_empty():
            self.logger.info(
                "[%s] profile old map %s not used for NS update (names are authoritative)",
                self.type_name,
                old.format(),
            )
        changed = self.apply_manual("", new.ipv4)
        if not changed:
            self.logger.info("[%s] all nameservers already at %s", self.type_name, new.ipv4)
        return changed

    def apply_manual(self, old_ip: str, new_ip: str) -> bool:
        if not is_ipv4(new_ip):
            raise ValueError(f"invalid IPv4: {new_ip}")

        api = self.config.get("api", "").lower()
        if api != "namecheap":
            raise RuntimeError(f"unsupported api: {api}")

        mapping = self._get_namecheap_ips()
        self.logger.info(
            "Namecheap nameservers %s: current mapping %s",
            ", ".join(self._ns_hosts()),
            ", ".join(f"{host}={mapping.get(host, '(none)')}" for host in self._ns_hosts()),
        )
        changed = False
        for host in self._ns_hosts():
            current = mapping.get(host)
            if current == new_ip:
                self.logger.info("Nameserver %s already set to %s", host, new_ip)
                continue
            self.logger.info("Updating nameserver %s: %s -> %s", host, current or "(none)", new_ip)
            self._set_namecheap_ip(host, new_ip, current=current)
            changed = True
        return changed
