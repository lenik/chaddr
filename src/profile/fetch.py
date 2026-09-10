"""Progressive profile address fetch for the GUI."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterator

from chaddr.address import is_ipv4, is_ipv6
from chaddr.profile.history import format_addr_history_timestamp
from chaddr.profile.instance import (
    canonical_from_type_label,
    handler_config_for_instance_from,
    is_instance_from_type,
)
from chaddr.profile.model import Profile
from chaddr.profile.parse import merge_cli_options, profile_from_blocks
from chaddr.profile_lexer import canonical_ws_tokens


@dataclass(frozen=True)
class ProfileAddressFetchEvent:
    fraction: float
    message: str
    entries: list["AddressEntry"]
    replace_sources: frozenset[str] | None = None


def iter_profile_address_fetch(
    profile: Profile,
    cli_options: dict | None = None,
    proxy: str | None = None,
    logger: logging.Logger | None = None,
) -> Iterator[ProfileAddressFetchEvent]:
    """Yield address batches for progressive GUI loading (history → resolve → instance)."""
    from chaddr.address import AddressEntry, resolve_from

    log = logger or logging.getLogger("chaddr")
    options = cli_options or {}

    steps: list[tuple[str, frozenset[str] | None, Callable[[], list["AddressEntry"]]]] = []

    history: list[AddressEntry] = []
    for record in profile.addr_history_records():
        if is_ipv4(record.address) or is_ipv6(record.address):
            history.append(AddressEntry.from_history_ip(record.address, record.timestamp))
    if history:
        steps.append(("Loading address history...", frozenset({"history"}), lambda h=history: h))

    old_ip = options.get("old_ip")
    if old_ip and (is_ipv4(str(old_ip)) or is_ipv6(str(old_ip))):
        steps.append(
            (
                "Loading old IP...",
                frozenset({"old-ip"}),
                lambda ip=str(old_ip): [AddressEntry.from_old_ip(ip)],
            )
        )

    resolve_blocks = [
        block
        for block in profile_from_blocks(profile)
        if canonical_ws_tokens(block.from_type).lower() == "resolve"
    ]
    for index, from_block in enumerate(resolve_blocks):
        hostname = (
            from_block.options.get("resolve")
            or from_block.options.get("host")
            or from_block.options.get("name")
            or "hostname"
        )

        def _resolve_entries(block=from_block) -> list[AddressEntry]:
            resolved = resolve_from("resolve", block.options)
            return AddressEntry.from_address_set(
                resolved,
                "resolve",
                timestamp=format_addr_history_timestamp(),
            )

        steps.append(
            (
                f"Resolving {hostname}...",
                frozenset({"resolve"}) if index == 0 else None,
                _resolve_entries,
            )
        )

    instance_blocks = [
        block for block in profile_from_blocks(profile) if is_instance_from_type(block.from_type)
    ]
    for index, from_block in enumerate(instance_blocks):
        source_label = canonical_from_type_label(from_block.from_type)
        scope_id = (
            from_block.options.get("nic")
            or from_block.options.get("eni")
            or from_block.options.get("network_interface")
            or from_block.options.get("network_interface_id")
            or from_block.options.get("instance")
            or from_block.options.get("instance_id")
            or source_label
        )

        def _instance_entries(block=from_block, source=source_label) -> list[AddressEntry]:
            from chaddr.types import create_handler

            spec = handler_config_for_instance_from(profile, block)
            if spec is None:
                return []
            handler_type, config = spec
            handler = create_handler(
                handler_type,
                config,
                merge_cli_options(profile, options or {}),
                proxy,
                log,
            )
            diag = handler.diagnose()
            detail = (
                block.options.get("nic")
                or block.options.get("eni")
                or block.options.get("network_interface")
                or block.options.get("network_interface_id")
                or block.options.get("instance")
                or block.options.get("instance_id")
                or ""
            )
            entries: list[AddressEntry] = []
            seen: set[str] = set()
            for ip in diag.addresses:
                if ip in seen:
                    continue
                seen.add(ip)
                if is_ipv4(ip):
                    entries.append(AddressEntry("IPv4", ip, source, detail=detail))
                elif is_ipv6(ip):
                    entries.append(AddressEntry("IPv6", ip, source, detail=detail))
            return entries

        steps.append(
            (
                f"Fetching {scope_id}...",
                frozenset({source_label}),
                _instance_entries,
            )
        )

    if not steps:
        yield ProfileAddressFetchEvent(1.0, "No profile addresses", [], None)
        return

    total = len(steps)
    for index, (message, replace_sources, fetch) in enumerate(steps):
        try:
            entries = fetch()
        except Exception as exc:
            log.warning("Address fetch skipped for %s: %s", profile.name, exc)
            entries = []
        yield ProfileAddressFetchEvent(
            (index + 1) / total,
            message,
            entries,
            replace_sources,
        )


def list_profile_candidate_addresses(
    profile: Profile,
    cli_options: dict | None = None,
    proxy: str | None = None,
    logger: logging.Logger | None = None,
) -> list["AddressEntry"]:
    """Collect all candidate addresses for a profile with source labels."""
    from chaddr.address import AddressEntry

    entries: list[AddressEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for event in iter_profile_address_fetch(profile, cli_options, proxy, logger):
        if event.replace_sources:
            entries = [entry for entry in entries if entry.source not in event.replace_sources]
            seen = {(entry.family, entry.address, entry.source) for entry in entries}
        for entry in event.entries:
            key = (entry.family, entry.address, entry.source)
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
    return entries
