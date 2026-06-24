"""Profile orchestration for diagnose, manual apply, and reallocate flows."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from chaddr.address import AddressSet, SpareFromAddresses, is_ipv4, is_ipv6, resolve_from
from chaddr.profile import Profile, merge_cli_options, resolve_profile_addresses
from chaddr.types.base import DiagnoseItem, DiagnoseResult, ReallocateResult, is_ipv4, is_ipv6
from chaddr.types import create_handler, get_handler_class

ProgressCallback = Callable[[float, str], None]
DiagnoseResultCallback = Callable[["DiagnoseResult"], None]


@dataclass
class ProfileRunResult:
    profile_name: str
    ok: bool
    message: str
    diagnose_results: list[DiagnoseResult] = field(default_factory=list)
    source_addresses: AddressSet | None = None
    old_addresses: AddressSet | None = None
    new_addresses: AddressSet | None = None
    old_ip: str | None = None
    new_ip: str | None = None


def _build_handlers(
    profile: Profile,
    cli_options: dict,
    proxy: str | None,
    logger: logging.Logger,
    spare_from: SpareFromAddresses | None = None,
):
    options = merge_cli_options(profile, cli_options)
    handlers = []
    for entry in profile.entries:
        handler_cls = get_handler_class(entry.type)
        if handler_cls is None:
            raise ValueError(f"unsupported type {entry.type!r} in profile {profile.name}")
        handler = create_handler(entry.type, entry.config, options, proxy, logger)
        handler.set_profile_context(profile.name, profile.path)
        if spare_from is not None:
            handler.set_spare_from_addresses(spare_from)
        handlers.append(handler)
    return handlers


def _build_spare_from(profile: Profile, extra_sets: list[AddressSet] | None) -> SpareFromAddresses:
    sets: list[AddressSet] = []
    if profile.from_block is not None:
        try:
            sets.append(resolve_profile_addresses(profile))
        except Exception:
            pass
    if extra_sets:
        sets.extend(extra_sets)
    return SpareFromAddresses.from_address_sets(*sets)


def _diagnose_from_block(profile: Profile) -> DiagnoseResult | None:
    if profile.from_block is None:
        return None
    items: list[DiagnoseItem] = []
    addresses: list[str] = []
    from_type = profile.from_block.from_type
    items.append(DiagnoseItem("from", True, from_type))
    try:
        resolved = resolve_from(profile.from_block.from_type, profile.from_block.options)
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
    return DiagnoseResult(
        f"from: {from_type}",
        "ready" if ok else "issues found",
        ok,
        items,
        addresses,
    )


def _check_address_consistency(
    source: AddressSet,
    results: list[DiagnoseResult],
    spare: SpareFromAddresses | None = None,
) -> tuple[bool, str]:
    if source.is_empty():
        return True, "all checks passed"

    allowed_v4 = {ip for ip in ([source.ipv4] if source.ipv4 else []) + (spare.ipv4 if spare else []) if ip}
    allowed_v6 = {ip for ip in ([source.ipv6] if source.ipv6 else []) + (spare.ipv6 if spare else []) if ip}

    mismatched: list[str] = []
    for result in results:
        if result.type_name.startswith("from:"):
            continue
        if result.type_name in ("aws elastic ip", "aliyun elastic ip"):
            continue
        type_v4 = {ip for ip in result.addresses if is_ipv4(ip)}
        type_v6 = {ip for ip in result.addresses if is_ipv6(ip)}
        if type_v4 and allowed_v4 and type_v4.isdisjoint(allowed_v4):
            mismatched.append(f"{result.type_name} IPv4: {', '.join(sorted(type_v4))}")
        if type_v6 and allowed_v6 and type_v6.isdisjoint(allowed_v6):
            mismatched.append(f"{result.type_name} IPv6: {', '.join(sorted(type_v6))}")

    if mismatched:
        return False, f"address mismatch vs from/spare ({source.format()}): " + "; ".join(mismatched)
    return True, "all checks passed"


def _accumulating_spare_sets(
    profile: Profile,
    extra_sets: list[AddressSet] | None,
) -> list[AddressSet]:
    accumulated: list[AddressSet] = []
    if profile.from_block is not None:
        try:
            accumulated.append(resolve_profile_addresses(profile))
        except Exception:
            pass
    accumulated.extend(profile.addr_history_sets())
    if extra_sets:
        accumulated.extend(extra_sets)
    return accumulated


def _spare_from_sets(accumulated: list[AddressSet]) -> SpareFromAddresses:
    return SpareFromAddresses.from_address_sets(*accumulated)


def _extend_spare_from_addresses(accumulated: list[AddressSet], result: DiagnoseResult) -> None:
    for ip in result.addresses:
        if is_ipv4(ip):
            accumulated.append(AddressSet(ipv4=ip))
        elif is_ipv6(ip):
            accumulated.append(AddressSet(ipv6=ip))


def diagnose_profile(
    profile: Profile,
    cli_options: dict | None = None,
    proxy: str | None = None,
    logger: logging.Logger | None = None,
    progress: ProgressCallback | None = None,
    spare_from_sets: list[AddressSet] | None = None,
    on_result: DiagnoseResultCallback | None = None,
) -> ProfileRunResult:
    log = logger or logging.getLogger("chaddr")
    accumulated = _accumulating_spare_sets(profile, spare_from_sets)
    handlers = _build_handlers(profile, cli_options or {}, proxy, log, _spare_from_sets(accumulated))
    results: list[DiagnoseResult] = []
    source = AddressSet()

    from_diag = _diagnose_from_block(profile)
    if from_diag:
        results.append(from_diag)
        if on_result:
            on_result(from_diag)
        _extend_spare_from_addresses(accumulated, from_diag)
        if from_diag.ok:
            source = AddressSet(
                ipv4=next((ip for ip in from_diag.addresses if is_ipv4(ip)), None),
                ipv6=next((ip for ip in from_diag.addresses if is_ipv6(ip)), None),
            )

    total = max(len(handlers) + (1 if from_diag else 0), 1)
    offset = 1 if from_diag else 0

    for index, handler in enumerate(handlers):
        handler.set_spare_from_addresses(_spare_from_sets(accumulated))
        if progress:
            progress((offset + index) / total, f"Diagnosing {handler.type_name}")
        handler.set_progress_callback(progress)
        handler.set_source_addresses(source if not source.is_empty() else None)
        diag_result = handler.diagnose()
        results.append(diag_result)
        _extend_spare_from_addresses(accumulated, diag_result)
        if on_result:
            on_result(diag_result)

    if progress:
        progress(1.0, f"Diagnosis complete for {profile.name}")

    ok = all(result.ok for result in results)
    message = "all checks passed" if ok else "issues found"

    consistent, consistency_msg = _check_address_consistency(source, results, _spare_from_sets(accumulated))
    if not consistent:
        ok = False
        message = consistency_msg

    return ProfileRunResult(
        profile_name=profile.name,
        ok=ok,
        message=message,
        diagnose_results=results,
        source_addresses=source if not source.is_empty() else None,
    )


def apply_address_profile(
    profile: Profile,
    new_addresses: AddressSet,
    cli_options: dict | None = None,
    proxy: str | None = None,
    logger: logging.Logger | None = None,
    progress: ProgressCallback | None = None,
    spare_from_sets: list[AddressSet] | None = None,
) -> ProfileRunResult:
    log = logger or logging.getLogger("chaddr")
    if new_addresses.is_empty():
        return ProfileRunResult(profile.name, False, "no new addresses provided")

    if not profile.has_manual_types():
        return ProfileRunResult(profile.name, False, "profile has no manually editable types")

    try:
        old_addresses = resolve_profile_addresses(profile)
    except Exception as exc:
        return ProfileRunResult(profile.name, False, f"could not resolve current addresses: {exc}")

    if old_addresses.is_empty():
        diagnose = diagnose_profile(profile, cli_options, proxy, log, spare_from_sets=spare_from_sets)
        for result in diagnose.diagnose_results:
            if result.type_name in ("aws elastic ip", "aliyun elastic ip"):
                v4 = next((ip for ip in result.addresses if is_ipv4(ip)), None)
                if v4:
                    old_addresses = AddressSet(ipv4=v4, ipv6=old_addresses.ipv6)
        if old_addresses.is_empty():
            return ProfileRunResult(
                profile.name,
                False,
                "could not determine current addresses; add from: resolve to profile",
                diagnose.diagnose_results,
            )

    handlers = _build_handlers(
        profile,
        cli_options or {},
        proxy,
        log,
        _build_spare_from(profile, spare_from_sets),
    )
    manual_handlers = [h for h in handlers if h.supports_manual_edit]
    total = max(len(manual_handlers), 1)

    for index, handler in enumerate(manual_handlers):
        if progress:
            progress(index / total, f"Applying {handler.type_name}")
        handler.set_progress_callback(progress)
        handler.apply_address_map(old_addresses, new_addresses)

    if progress:
        progress(1.0, f"Applied {old_addresses.format()} -> {new_addresses.format()}")

    return ProfileRunResult(
        profile_name=profile.name,
        ok=True,
        message=f"applied {old_addresses.format()} -> {new_addresses.format()}",
        old_addresses=old_addresses,
        new_addresses=new_addresses,
        old_ip=old_addresses.ipv4,
        new_ip=new_addresses.ipv4,
    )


def apply_manual_profile(
    profile: Profile,
    new_ip: str,
    old_ip: str | None = None,
    cli_options: dict | None = None,
    proxy: str | None = None,
    logger: logging.Logger | None = None,
    progress: ProgressCallback | None = None,
) -> ProfileRunResult:
    new_addresses = AddressSet(ipv4=new_ip if is_ipv4(new_ip) else None, ipv6=new_ip if is_ipv6(new_ip) else None)
    if new_addresses.is_empty():
        return ProfileRunResult(profile.name, False, f"invalid IP address: {new_ip}")
    result = apply_address_profile(profile, new_addresses, cli_options, proxy, logger, progress)
    if old_ip and result.ok and result.old_addresses:
        pass
    return result


def reallocate_profile(
    profile: Profile,
    new_override: AddressSet | None = None,
    cli_options: dict | None = None,
    proxy: str | None = None,
    logger: logging.Logger | None = None,
    progress: ProgressCallback | None = None,
    spare_from_sets: list[AddressSet] | None = None,
) -> ProfileRunResult:
    log = logger or logging.getLogger("chaddr")
    handlers = _build_handlers(
        profile,
        cli_options or {},
        proxy,
        log,
        _build_spare_from(profile, spare_from_sets),
    )

    reallocate_handlers = [handler for handler in handlers if handler.supports_reallocate]
    if not reallocate_handlers:
        return ProfileRunResult(profile.name, False, "profile has no reallocate-capable types")

    try:
        old_source = resolve_profile_addresses(profile)
    except Exception:
        old_source = AddressSet()

    if progress:
        progress(0.05, f"Starting reallocate for {profile.name}")

    realloc_result: ReallocateResult | None = None
    for handler in reallocate_handlers:
        handler.set_progress_callback(progress)
        realloc_result = handler.reallocate()
        if not realloc_result.ok:
            return ProfileRunResult(
                profile.name,
                False,
                realloc_result.message or "reallocate failed",
            )

    old_ip = realloc_result.old_ip if realloc_result else None
    new_ip = realloc_result.new_ip if realloc_result else None
    if not old_ip or not new_ip:
        return ProfileRunResult(profile.name, False, "reallocate did not return old/new IP")

    old_addresses = AddressSet(
        ipv4=old_ip,
        ipv6=old_source.ipv6,
    )
    new_addresses = AddressSet(
        ipv4=new_ip,
        ipv6=(new_override.ipv6 if new_override and new_override.ipv6 else old_source.ipv6),
    )
    if new_override and new_override.ipv4:
        new_addresses = AddressSet(ipv4=new_override.ipv4, ipv6=new_addresses.ipv6)

    update_handlers = [handler for handler in handlers if handler.supports_manual_edit]
    total = max(len(update_handlers), 1)
    for index, handler in enumerate(update_handlers):
        if progress:
            fraction = 0.5 + (0.5 * index / total)
            progress(fraction, f"Updating {handler.type_name}")
        handler.set_progress_callback(progress)
        handler.update_address_map(old_addresses, new_addresses)

    if progress:
        progress(1.0, f"Reallocate complete: {old_addresses.format()} -> {new_addresses.format()}")

    return ProfileRunResult(
        profile.name,
        True,
        f"reallocated {old_addresses.format()} -> {new_addresses.format()}",
        old_addresses=old_addresses,
        new_addresses=new_addresses,
        old_ip=old_ip,
        new_ip=new_ip,
    )
