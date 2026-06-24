"""Address set and resolution helpers."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field


@dataclass
class AddressSet:
    ipv4: str | None = None
    ipv6: str | None = None

    def all(self) -> list[str]:
        return [addr for addr in (self.ipv4, self.ipv6) if addr]

    def is_empty(self) -> bool:
        return not self.ipv4 and not self.ipv6

    def format(self) -> str:
        parts = []
        if self.ipv4:
            parts.append(f"IPv4={self.ipv4}")
        if self.ipv6:
            parts.append(f"IPv6={self.ipv6}")
        return ", ".join(parts) if parts else "(none)"

    def with_overrides(self, override: AddressSet | None) -> AddressSet:
        if override is None or override.is_empty():
            return AddressSet(ipv4=self.ipv4, ipv6=self.ipv6)
        return AddressSet(
            ipv4=override.ipv4 or self.ipv4,
            ipv6=override.ipv6 or self.ipv6,
        )


@dataclass
class SpareFromAddresses:
    """Alternate from-source addresses (e.g. GUI current + profile resolve)."""

    ipv4: list[str] = field(default_factory=list)
    ipv6: list[str] = field(default_factory=list)

    def all(self) -> list[str]:
        return [*self.ipv4, *self.ipv6]

    def is_empty(self) -> bool:
        return not self.ipv4 and not self.ipv6

    @classmethod
    def from_address_sets(cls, *sets: AddressSet | None) -> SpareFromAddresses:
        ipv4: list[str] = []
        ipv6: list[str] = []
        for item in sets:
            if item is None or item.is_empty():
                continue
            if item.ipv4 and item.ipv4 not in ipv4:
                ipv4.append(item.ipv4)
            if item.ipv6 and item.ipv6 not in ipv6:
                ipv6.append(item.ipv6)
        return cls(ipv4=ipv4, ipv6=ipv6)


@dataclass
class AddressEntry:
    family: str
    address: str
    source: str = "manual"
    spare: bool = False

    def display(self) -> str:
        return f"{self.family}:{self.address} ({self.source})"

    @classmethod
    def from_old_ip(cls, old_ip: str) -> AddressEntry:
        family = "IPv4" if is_ipv4(old_ip) else "IPv6"
        return cls(family, old_ip, "old-ip", spare=True)

    @classmethod
    def from_history_ip(cls, ip: str) -> AddressEntry:
        family = "IPv4" if is_ipv4(ip) else "IPv6"
        return cls(family, ip, "addr-history", spare=True)

    @classmethod
    def from_address_set(cls, addresses: AddressSet, source: str = "resolve") -> list[AddressEntry]:
        entries: list[AddressEntry] = []
        if addresses.ipv4:
            entries.append(cls("IPv4", addresses.ipv4, source))
        if addresses.ipv6:
            entries.append(cls("IPv6", addresses.ipv6, source))
        return entries

    @classmethod
    def parse_display(cls, text: str) -> AddressEntry | None:
        text = text.strip()
        if not text:
            return None
        family, rest = text.split(":", 1)
        family = family.strip()
        if family not in ("IPv4", "IPv6"):
            return None
        if " (" not in rest or not rest.endswith(")"):
            return None
        address, source_part = rest.rsplit(" (", 1)
        source = source_part[:-1]
        address = address.strip()
        source = source.strip()
        if not address or not source:
            return None
        return cls(family, address, source)


def address_set_from_entries(entries: list[AddressEntry]) -> AddressSet:
    ipv4 = next((entry.address for entry in entries if entry.family == "IPv4"), None)
    ipv6 = next((entry.address for entry in entries if entry.family == "IPv6"), None)
    return AddressSet(ipv4=ipv4, ipv6=ipv6)


def spare_sets_from_entries(entries: list[AddressEntry]) -> list[AddressSet]:
    sets: list[AddressSet] = []
    for entry in entries:
        if not entry.spare:
            continue
        if entry.family == "IPv4":
            sets.append(AddressSet(ipv4=entry.address))
        else:
            sets.append(AddressSet(ipv6=entry.address))
    return sets


def is_ipv4(value: str | None) -> bool:
    if not value:
        return False
    try:
        return ipaddress.ip_address(value.strip()).version == 4
    except ValueError:
        return False


def is_ipv6(value: str | None) -> bool:
    if not value:
        return False
    try:
        return ipaddress.ip_address(value.strip()).version == 6
    except ValueError:
        return False


def parse_address_set(ipv4: str | None = None, ipv6: str | None = None) -> AddressSet:
    v4 = ipv4.strip() if ipv4 and ipv4.strip() else None
    v6 = ipv6.strip() if ipv6 and ipv6.strip() else None
    if v4 and not is_ipv4(v4):
        raise ValueError(f"invalid IPv4: {v4}")
    if v6 and not is_ipv6(v6):
        raise ValueError(f"invalid IPv6: {v6}")
    return AddressSet(ipv4=v4, ipv6=v6)


def resolve_hostname(hostname: str) -> AddressSet:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RuntimeError(f"resolve failed for {hostname!r}: {exc}") from exc

    ipv4: str | None = None
    ipv6: str | None = None
    for family, _type, _proto, _canon, sockaddr in infos:
        ip = sockaddr[0]
        if ip.startswith("::ffff:"):
            ip = ip.rsplit(":", 1)[-1]
        if family == socket.AF_INET and ipv4 is None:
            ipv4 = ip
        elif family == socket.AF_INET6 and ipv6 is None:
            ipv6 = ip.split("%", 1)[0]
    return AddressSet(ipv4=ipv4, ipv6=ipv6)


def resolve_from(from_type: str, options: dict[str, str]) -> AddressSet:
    if from_type == "resolve":
        hostname = options.get("resolve") or options.get("host") or options.get("name")
        if not hostname:
            raise RuntimeError('from: resolve requires "resolve: <hostname>" in profile')
        return resolve_hostname(hostname)
    raise RuntimeError(f"unsupported from type: {from_type!r}")
