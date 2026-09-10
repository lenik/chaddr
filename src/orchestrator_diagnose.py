"""Diagnose helpers for profile orchestration."""

from __future__ import annotations

import logging

from chaddr.address import AddressSet, SpareFromAddresses, is_ipv4, is_ipv6, resolve_from
from chaddr.profile import (
    Profile,
    ProfileFromBlock,
    handler_config_for_instance_from,
    is_instance_from_type,
    merge_cli_options,
    profile_from_blocks,
    resolve_profile_addresses,
    instance_profile_addresses,
)
from chaddr.profile_lexer import canonical_ws_tokens
from chaddr.types.base import DiagnoseItem, DiagnoseResult
from chaddr.types import create_handler

def _diagnose_resolve_from_blocks(profile: Profile) -> DiagnoseResult | None:
    resolve_blocks = [
        block
        for block in profile_from_blocks(profile)
        if canonical_ws_tokens(block.from_type).lower() == "resolve"
    ]
    if not resolve_blocks:
        return None
    items: list[DiagnoseItem] = []
    addresses: list[str] = []
    for from_block in resolve_blocks:
        from_type = from_block.from_type
        items.append(DiagnoseItem("from", True, from_type))
        try:
            resolved = resolve_from("resolve", from_block.options)
            if resolved.ipv4:
                addresses.append(resolved.ipv4)
                items.append(DiagnoseItem("IPv4", True, resolved.ipv4))
            if resolved.ipv6:
                addresses.append(resolved.ipv6)
                items.append(DiagnoseItem("IPv6", True, resolved.ipv6))
            if resolved.is_empty():
                items.append(
                    DiagnoseItem(
                        "resolve",
                        False,
                        "no addresses returned",
                        "Check the hostname or DNS configuration.",
                    )
                )
        except Exception as exc:
            items.append(
                DiagnoseItem(
                    "resolve",
                    False,
                    str(exc),
                    "Verify from-block settings (e.g. resolve: hostname).",
                )
            )
    ok = all(item.ok for item in items)
    label = resolve_blocks[0].from_type if len(resolve_blocks) == 1 else "resolve"
    return DiagnoseResult(
        f"from: {label}",
        "ready" if ok else "issues found",
        ok,
        items,
        addresses,
    )


def _diagnose_instance_from_block(
    profile: Profile,
    from_block: ProfileFromBlock,
    cli_options: dict | None,
    proxy: str | None,
    logger: logging.Logger,
) -> DiagnoseResult:
    from_type = from_block.from_type
    items: list[DiagnoseItem] = [DiagnoseItem("from", True, from_type)]
    addresses: list[str] = []
    spec = handler_config_for_instance_from(profile, from_block)
    if spec is None:
        items.append(
            DiagnoseItem(
                "instance",
                False,
                f"unsupported from type: {from_type!r}",
                "Use from: ec2 nic, from: ec2, from: aliyun nic, or from: aliyun.",
            )
        )
        return DiagnoseResult(f"from: {from_type}", "issues found", False, items, addresses)

    handler_type, config = spec
    try:
        handler = create_handler(handler_type, config, merge_cli_options(profile, cli_options or {}), proxy, logger)
        handler_diag = handler.diagnose()
        addresses.extend(handler_diag.addresses)
        for item in handler_diag.items:
            if item.label in (
                "region",
                "aws api",
                "aliyun api",
                "instance",
                "instance lookup",
                "nic",
                "nic lookup",
                "ec2",
                "ec2 nic",
                "aliyun",
                "aliyun nic",
                "ec2 lookup",
                "ec2 nic lookup",
                "aliyun lookup",
                "aliyun nic lookup",
            ):
                items.append(item)
        if not addresses:
            items.append(
                DiagnoseItem(
                    "instance",
                    False,
                    "no public IP returned",
                    "Start the instance or attach a public / Elastic IP.",
                )
            )
    except Exception as exc:
        items.append(
            DiagnoseItem(
                "instance",
                False,
                str(exc),
                "Check cloud credentials, region, and instance ID.",
            )
        )
    ok = all(item.ok for item in items)
    return DiagnoseResult(
        f"from: {from_type}",
        "ready" if ok else "issues found",
        ok,
        items,
        addresses,
    )


def _diagnose_all_from_blocks(
    profile: Profile,
    cli_options: dict | None,
    proxy: str | None,
    logger: logging.Logger,
) -> list[DiagnoseResult]:
    results: list[DiagnoseResult] = []
    resolve_diag = _diagnose_resolve_from_blocks(profile)
    if resolve_diag is not None:
        results.append(resolve_diag)
    for from_block in profile_from_blocks(profile):
        if is_instance_from_type(from_block.from_type):
            results.append(_diagnose_instance_from_block(profile, from_block, cli_options, proxy, logger))
    return results


def _merge_source_from_diags(diags: list[DiagnoseResult]) -> AddressSet:
    source = AddressSet()
    for diag in diags:
        if not diag.ok:
            continue
        ipv4 = next((ip for ip in diag.addresses if is_ipv4(ip)), None)
        ipv6 = next((ip for ip in diag.addresses if is_ipv6(ip)), None)
        source = AddressSet(ipv4=ipv4 or source.ipv4, ipv6=ipv6 or source.ipv6)
    return source


def _check_address_consistency(
    expected: AddressSet,
    results: list[DiagnoseResult],
    spare: SpareFromAddresses | None = None,
    *,
    strict: bool = False,
) -> tuple[bool, str]:
    if expected.is_empty():
        return True, "all checks passed"

    if strict:
        allowed_v4 = {expected.ipv4} if expected.ipv4 else set()
        allowed_v6 = {expected.ipv6} if expected.ipv6 else set()
        baseline = f"target ({expected.format()})"
    else:
        allowed_v4 = {
            ip for ip in ([expected.ipv4] if expected.ipv4 else []) + (spare.ipv4 if spare else []) if ip
        }
        allowed_v6 = {
            ip for ip in ([expected.ipv6] if expected.ipv6 else []) + (spare.ipv6 if spare else []) if ip
        }
        baseline = f"from/spare ({expected.format()})"

    mismatched: list[str] = []
    for result in results:
        if result.type_name.startswith("from:"):
            continue
        if result.type_name in ("aws elastic ip", "aliyun elastic ip", "registered nameserver"):
            continue
        type_v4 = {ip for ip in result.addresses if is_ipv4(ip)}
        type_v6 = {ip for ip in result.addresses if is_ipv6(ip)}
        if type_v4 and allowed_v4 and type_v4.isdisjoint(allowed_v4):
            mismatched.append(f"{result.type_name} IPv4: {', '.join(sorted(type_v4))}")
        if type_v6 and allowed_v6 and type_v6.isdisjoint(allowed_v6):
            mismatched.append(f"{result.type_name} IPv6: {', '.join(sorted(type_v6))}")

    if mismatched:
        return False, f"address mismatch vs {baseline}: " + "; ".join(mismatched)
    return True, "all checks passed"


def _accumulating_spare_sets(
    profile: Profile,
    extra_sets: list[AddressSet] | None,
) -> list[AddressSet]:
    accumulated: list[AddressSet] = []
    try:
        accumulated.append(resolve_profile_addresses(profile))
    except Exception:
        pass
    try:
        instance_addrs = instance_profile_addresses(profile)
        if not instance_addrs.is_empty():
            accumulated.append(instance_addrs)
    except Exception:
        pass
    accumulated.extend(profile.addr_history_sets())
    if extra_sets:
        accumulated.extend(extra_sets)
    return accumulated


def _spare_from_sets(accumulated: list[AddressSet]) -> SpareFromAddresses:
    return SpareFromAddresses.from_address_sets(*accumulated)


# Also grow spare from cloud / elastic IP diagnose results when those types are selected.
def _extend_spare_from_addresses(accumulated: list[AddressSet], result: DiagnoseResult) -> None:
    if result.type_name in ("zone file", "bind db", "hosts file", "file", "changelog", "router"):
        return
    for ip in result.addresses:
        if is_ipv4(ip):
            accumulated.append(AddressSet(ipv4=ip))
        elif is_ipv6(ip):
            accumulated.append(AddressSet(ipv6=ip))
