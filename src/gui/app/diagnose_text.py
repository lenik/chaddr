"""Diagnose result formatting helpers for the GUI."""

from __future__ import annotations

import logging

from chaddr.gui.diagnostics_format import mutable_action_lines
from chaddr.orchestrator import ProfileRunResult
from chaddr.types.base import DiagnoseResult
from chaddr.types.hosts_file import APPLY_TARGETS_LABEL


def _show_diagnose_address_footer(diag: DiagnoseResult) -> bool:
    """Omit redundant IP footer when addresses are already listed in diagnose items."""
    if diag.type_name in ("zone file", "bind db"):
        return False
    if diag.type_name == "hosts file":
        return False
    if diag.type_name == "file":
        return False
    if diag.type_name == "changelog":
        return False
    return True


def _format_diagnose_result(diag: DiagnoseResult) -> str:
    lines: list[str] = []
    status = "OK" if diag.ok else "FAIL"
    lines.append(f"[{status}] {diag.type_name}: {diag.summary}")
    lines.extend(mutable_action_lines(diag))
    for item in diag.items:
        if item.label == APPLY_TARGETS_LABEL:
            continue
        mark = "OK" if item.ok else "FAIL"
        lines.append(f"  [{mark}] {item.label}: {item.detail}")
        if not item.ok and item.guidance:
            lines.append(f"      -> {item.guidance}")
    if diag.addresses and _show_diagnose_address_footer(diag):
        lines.append(f"    IP: {', '.join(diag.addresses)}")
    return "\n".join(lines)


def _format_diagnose(result: ProfileRunResult) -> str:
    lines = [f"Profile: {result.profile_name}", f"Result: {result.message}", ""]
    if result.source_addresses and not result.source_addresses.is_empty():
        lines.append(f"From-source: {result.source_addresses.format()}")
    if result.new_addresses and not result.new_addresses.is_empty():
        lines.append(f"Target: {result.new_addresses.format()}")
    if (result.source_addresses and not result.source_addresses.is_empty()) or (
        result.new_addresses and not result.new_addresses.is_empty()
    ):
        lines.append("")
    for diag in result.diagnose_results:
        lines.append(_format_diagnose_result(diag))
    return "\n".join(lines)


def _log_diagnose(logger: logging.Logger, result: ProfileRunResult) -> None:
    logger.info("Profile %s: %s", result.profile_name, result.message)
    for diag in result.diagnose_results:
        status = "OK" if diag.ok else "FAIL"
        logger.info("  [%s] %s - %s", status, diag.type_name, diag.summary)
        for item in diag.items:
            prefix = "OK" if item.ok else "FAIL"
            logger.info("    [%s] %s: %s", prefix, item.label, item.detail)
            if not item.ok and item.guidance:
                logger.info("      -> %s", item.guidance)
