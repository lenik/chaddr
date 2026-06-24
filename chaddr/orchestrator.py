"""Profile orchestration for diagnose, manual apply, and reallocate flows."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from chaddr.address import AddressSet, SpareFromAddresses, is_ipv4, is_ipv6, resolve_from
from chaddr.profile import (
    Profile,
    ProfileFromBlock,
    append_profile_addr_history,
    handler_config_for_instance_from,
    is_instance_from_type,
    merge_cli_options,
    profile_from_blocks,
    resolve_profile_addresses,
    instance_profile_addresses,
)
from chaddr.profile_lexer import canonical_ws_tokens
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
    try:
        sets.append(resolve_profile_addresses(profile))
    except Exception:
        pass
    try:
        instance_addrs = instance_profile_addresses(profile)
        if not instance_addrs.is_empty():
            sets.append(instance_addrs)
    except Exception:
        pass
    if extra_sets:
        sets.extend(extra_sets)
    return SpareFromAddresses.from_address_sets(*sets)


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
                "Use from: ec2 instance or from: aliyun instance.",
            )
        )
        return DiagnoseResult(f"from: {from_type}", "issues found", False, items, addresses)

    handler_type, config = spec
    try:
        handler = create_handler(handler_type, config, merge_cli_options(profile, cli_options or {}), proxy, logger)
        handler_diag = handler.diagnose()
        addresses.extend(handler_diag.addresses)
        for item in handler_diag.items:
            if item.label in ("region", "aws api", "aliyun api", "instance", "instance lookup"):
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


def _extend_spare_from_addresses(accumulated: list[AddressSet], result: DiagnoseResult) -> None:
    if result.type_name in ("zone file", "bind db", "hosts file", "file"):
        return
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
    profile_spare = _spare_from_sets(list(accumulated))
    handlers = _build_handlers(profile, cli_options or {}, proxy, log, profile_spare)
    results: list[DiagnoseResult] = []
    source = AddressSet()

    from_diags = _diagnose_all_from_blocks(profile, cli_options, proxy, log)
    source = _merge_source_from_diags(from_diags)
    for from_diag in from_diags:
        results.append(from_diag)
        if on_result:
            on_result(from_diag)
        _extend_spare_from_addresses(accumulated, from_diag)

    total = max(len(handlers) + len(from_diags), 1)
    offset = len(from_diags)

    for index, handler in enumerate(handlers):
        handler.set_spare_from_addresses(_spare_from_sets(accumulated))
        handler.set_profile_spare_for_apply(profile_spare)
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


def _log_spare_from(logger: logging.Logger, spare: SpareFromAddresses, *, prefix: str = "") -> None:
    label = f"{prefix}spare" if prefix else "spare"
    if spare.is_empty():
        logger.info("%s from-sources: (none)", label)
        return
    parts: list[str] = []
    if spare.ipv4:
        parts.append("IPv4=[" + ", ".join(spare.ipv4) + "]")
    if spare.ipv6:
        parts.append("IPv6=[" + ", ".join(spare.ipv6) + "]")
    logger.info("%s from-sources: %s", label, ", ".join(parts))


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

    log.info("Apply profile %s: target %s", profile.name, new_addresses.format())
    if profile.path:
        log.info("Profile file: %s", profile.path)

    diagnose_results: list[DiagnoseResult] | None = None
    old_source = "unknown"

    try:
        old_addresses = resolve_profile_addresses(profile)
        if not old_addresses.is_empty():
            old_source = "resolve"
            log.info("Current addresses (resolve): %s", old_addresses.format())
    except Exception as exc:
        log.warning("DNS resolve for current addresses failed: %s", exc)
        old_addresses = AddressSet()

    if old_addresses.is_empty():
        try:
            old_addresses = instance_profile_addresses(profile, cli_options, proxy, log)
            if not old_addresses.is_empty():
                old_source = "instance"
                log.info("Current addresses (instance): %s", old_addresses.format())
        except Exception as exc:
            log.warning("Instance lookup for current addresses failed: %s", exc)

    if old_addresses.is_empty():
        log.info("Current addresses unknown; running diagnose to detect cloud/elastic IP...")
        diagnose = diagnose_profile(profile, cli_options, proxy, log, spare_from_sets=spare_from_sets)
        diagnose_results = diagnose.diagnose_results
        for result in diagnose.diagnose_results:
            log.info("  diagnose [%s] %s: %s", "OK" if result.ok else "FAIL", result.type_name, result.summary)
            if result.addresses:
                log.info("    addresses: %s", ", ".join(result.addresses))
        for result in diagnose.diagnose_results:
            if result.type_name in ("aws elastic ip", "aliyun elastic ip"):
                v4 = next((ip for ip in result.addresses if is_ipv4(ip)), None)
                if v4:
                    old_addresses = AddressSet(ipv4=v4, ipv6=old_addresses.ipv6)
                    old_source = result.type_name
                    log.info("Using current IPv4 from %s: %s", result.type_name, v4)
        if old_addresses.is_empty():
            try:
                old_addresses = instance_profile_addresses(profile, cli_options, proxy, log)
                if not old_addresses.is_empty():
                    old_source = "instance"
                    log.info("Current addresses (instance): %s", old_addresses.format())
            except Exception as exc:
                log.warning("Instance lookup for current addresses failed: %s", exc)
        if old_addresses.is_empty():
            return ProfileRunResult(
                profile.name,
                False,
                "could not determine current addresses; add from: resolve or from: ec2/aliyun instance",
                diagnose_results,
            )

    log.info("Apply map (old source: %s): %s -> %s", old_source, old_addresses.format(), new_addresses.format())

    profile_spare = _build_spare_from(profile, spare_from_sets)
    _log_spare_from(log, profile_spare)
    if spare_from_sets:
        log.info("GUI/panel spare sets: %d", len(spare_from_sets))

    handlers = _build_handlers(
        profile,
        cli_options or {},
        proxy,
        log,
        profile_spare,
    )
    manual_handlers = [h for h in handlers if h.supports_manual_edit]
    skipped = [h.type_name for h in handlers if not h.supports_manual_edit]
    if skipped:
        log.info("Skipping non-manual types: %s", ", ".join(skipped))
    if not manual_handlers:
        return ProfileRunResult(profile.name, False, "no manually editable handlers in profile")

    log.info("Manual apply targets (%d): %s", len(manual_handlers), ", ".join(h.type_name for h in manual_handlers))
    total = max(len(manual_handlers), 1)
    changed_count = 0

    for index, handler in enumerate(manual_handlers):
        handler.set_profile_spare_for_apply(profile_spare)
        if progress:
            progress(index / total, f"Applying {handler.type_name}")
        handler.set_progress_callback(progress)
        log.info("--- Applying %s (%d/%d) ---", handler.type_name, index + 1, len(manual_handlers))
        try:
            if handler.apply_address_map(old_addresses, new_addresses):
                changed_count += 1
        except Exception as exc:
            log.exception("Apply failed for %s: %s", handler.type_name, exc)
            return ProfileRunResult(
                profile.name,
                False,
                f"apply failed for {handler.type_name}: {exc}",
                diagnose_results,
                old_addresses=old_addresses,
                new_addresses=new_addresses,
            )

    if progress:
        progress(1.0, f"Applied {old_addresses.format()} -> {new_addresses.format()}")

    if changed_count == 0:
        log.warning("Apply finished: no handler wrote changes (0/%d updated)", len(manual_handlers))
    else:
        log.info("Apply finished: %d/%d handler(s) updated", changed_count, len(manual_handlers))

    return ProfileRunResult(
        profile_name=profile.name,
        ok=True,
        message=f"applied {old_addresses.format()} -> {new_addresses.format()} ({changed_count}/{len(manual_handlers)} updated)",
        diagnose_results=diagnose_results,
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
        handler.set_source_addresses(old_source)
        handler.update_address_map(old_addresses, new_addresses)

    if progress:
        progress(1.0, f"Reallocate complete: {old_addresses.format()} -> {new_addresses.format()}")

    if profile.path:
        try:
            if append_profile_addr_history(profile.path, new_addresses.all()):
                log.info("Updated addr-history in %s", profile.path)
        except OSError as exc:
            log.warning("Could not update addr-history in %s: %s", profile.path, exc)

    return ProfileRunResult(
        profile.name,
        True,
        f"reallocated {old_addresses.format()} -> {new_addresses.format()}",
        old_addresses=old_addresses,
        new_addresses=new_addresses,
        old_ip=old_ip,
        new_ip=new_ip,
    )
