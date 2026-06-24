"""Base classes for address type handlers."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from chaddr.address import AddressSet, SpareFromAddresses, is_ipv4, is_ipv6

__all__ = ["is_ipv4", "is_ipv6", "AddressSet"]


ProgressCallback = Callable[[float, str], None]


@dataclass
class DiagnoseItem:
    label: str
    ok: bool
    detail: str
    guidance: str = ""


@dataclass
class DiagnoseResult:
    type_name: str
    summary: str
    ok: bool
    items: list[DiagnoseItem] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)


@dataclass
class ReallocateResult:
    ok: bool
    old_ip: str | None = None
    new_ip: str | None = None
    message: str = ""


IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"
)


class AddressTypeHandler(ABC):
    type_name: str = ""
    supports_manual_edit: bool = False
    supports_reallocate: bool = False

    def __init__(
        self,
        config: dict[str, str],
        options: dict[str, Any],
        proxy: str | None,
        logger: logging.Logger,
    ) -> None:
        self.config = config
        self.options = options
        self.proxy = proxy
        self.logger = logger
        self._progress: ProgressCallback | None = None
        self._source_addresses: AddressSet | None = None
        self._spare_from_addresses: SpareFromAddresses | None = None
        self._profile_spare_for_apply: SpareFromAddresses | None = None
        self._profile_name: str | None = None
        self._profile_path: "Path | None" = None

    def set_profile_context(self, profile_name: str, profile_path) -> None:
        self._profile_name = profile_name
        self._profile_path = profile_path

    def set_source_addresses(self, source: AddressSet | None) -> None:
        self._source_addresses = source

    def set_spare_from_addresses(self, spare: SpareFromAddresses | None) -> None:
        self._spare_from_addresses = spare

    def set_profile_spare_for_apply(self, spare: SpareFromAddresses | None) -> None:
        """Profile-scoped spare for Apply preview (not grown during diagnose)."""
        self._profile_spare_for_apply = spare

    def _apply_match_spare(self) -> SpareFromAddresses:
        if self._profile_spare_for_apply is not None and not self._profile_spare_for_apply.is_empty():
            return self._profile_spare_for_apply
        if self._source_addresses and not self._source_addresses.is_empty():
            return SpareFromAddresses.from_address_sets(self._source_addresses)
        return SpareFromAddresses()

    def set_progress_callback(self, callback: ProgressCallback | None) -> None:
        self._progress = callback

    def report_progress(self, fraction: float, message: str) -> None:
        self.logger.info(message)
        if self._progress:
            self._progress(max(0.0, min(1.0, fraction)), message)

    @abstractmethod
    def diagnose(self) -> DiagnoseResult:
        raise NotImplementedError

    def apply_manual(self, old_ip: str, new_ip: str) -> bool:
        raise NotImplementedError(f"{self.type_name} does not support manual edit")

    def apply_address_map(self, old: AddressSet, new: AddressSet) -> bool:
        target = self.config.get("path") or self.config.get("zone") or self.config.get("host") or ""
        where = f" ({target})" if target else ""
        self.logger.info("[%s] apply%s: %s -> %s", self.type_name, where, old.format(), new.format())
        changed = False
        if old.ipv4 and new.ipv4:
            if old.ipv4 == new.ipv4:
                self.logger.info(
                    "[%s] primary IPv4 equals new (%s); scanning spare/old IPs in target",
                    self.type_name,
                    old.ipv4,
                )
            if self.apply_manual(old.ipv4, new.ipv4):
                self.logger.info("[%s] IPv4 updated: %s -> %s", self.type_name, old.ipv4, new.ipv4)
                changed = True
            elif old.ipv4 != new.ipv4:
                self.logger.warning("[%s] IPv4 unchanged: no match for %s", self.type_name, old.ipv4)
        elif new.ipv4 and not old.ipv4:
            self.logger.warning("[%s] skip IPv4: no old IPv4 in apply map (new=%s)", self.type_name, new.ipv4)
        if old.ipv6 and new.ipv6:
            if old.ipv6 == new.ipv6:
                self.logger.info(
                    "[%s] primary IPv6 equals new (%s); scanning spare/old IPs in target",
                    self.type_name,
                    old.ipv6,
                )
            if self.apply_manual(old.ipv6, new.ipv6):
                self.logger.info("[%s] IPv6 updated: %s -> %s", self.type_name, old.ipv6, new.ipv6)
                changed = True
            elif old.ipv6 != new.ipv6:
                self.logger.warning("[%s] IPv6 unchanged: no match for %s", self.type_name, old.ipv6)
        elif new.ipv6 and not old.ipv6:
            self.logger.warning("[%s] skip IPv6: no old IPv6 in apply map (new=%s)", self.type_name, new.ipv6)
        if not changed:
            self.logger.warning("[%s] no changes written", self.type_name)
        return changed

    def reallocate(self) -> ReallocateResult:
        raise NotImplementedError(f"{self.type_name} does not support reallocate")

    def update_ip(self, old_ip: str, new_ip: str) -> bool:
        if not self.supports_manual_edit:
            return True
        return self.apply_manual(old_ip, new_ip)

    def update_address_map(self, old: AddressSet, new: AddressSet) -> bool:
        if not self.supports_manual_edit:
            return True
        return self.apply_address_map(old, new)
