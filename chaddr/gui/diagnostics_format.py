"""Diagnostic summary formatting helpers."""

from __future__ import annotations

from chaddr.types import get_handler_class
from chaddr.types.base import DiagnoseResult
from chaddr.types.hosts_file import APPLY_TARGETS_LABEL


def diag_item_detail(diag: DiagnoseResult, label: str) -> str:
    for item in diag.items:
        if item.label.lower() == label.lower():
            return item.detail.strip()
    return ""


def _apply_target_lines(diag: DiagnoseResult) -> list[str]:
    detail = diag_item_detail(diag, APPLY_TARGETS_LABEL)
    if not detail:
        return []
    return [line for line in detail.splitlines() if line.strip()]


def mutable_action_lines(diag: DiagnoseResult) -> list[str]:
    """Lines describing what Renew or Apply may change (shown in diagnostics)."""
    handler_cls = get_handler_class(diag.type_name)
    if handler_cls is None:
        return []

    lines: list[str] = []
    if handler_cls.supports_reallocate:
        line = _renew_line(diag)
        if line:
            lines.append(line)
    if handler_cls.supports_manual_edit:
        lines.extend(_apply_lines(diag))
    return lines


def _apply_lines(diag: DiagnoseResult) -> list[str]:
    type_name = diag.type_name
    if type_name == "hosts file":
        path = diag_item_detail(diag, "path")
        target = path or "hosts file"
        targets = _apply_target_lines(diag)
        header = f"  [Apply] Replaces matching IP entries in {target}"
        if targets:
            return [header + ":", *[f"> {line}" for line in targets]]
        return [header]
    if type_name in ("zone file", "bind db"):
        path = diag_item_detail(diag, "path")
        target = path or "zone file"
        targets = _apply_target_lines(diag)
        header = f"  [Apply] Replaces IP addresses in BIND zone file {target}"
        if targets:
            return [header + ":", *[f"> {line}" for line in targets]]
        return [header]
    if type_name == "registered nameserver":
        nameservers = diag_item_detail(diag, "nameservers")
        if nameservers:
            return [f"  [Apply] Updates Namecheap glue IP for {nameservers}"]
        return ["  [Apply] Updates Namecheap registered nameserver glue records"]
    return [f"  [Apply] Updates {type_name}"]


def _renew_line(diag: DiagnoseResult) -> str:
    type_name = diag.type_name
    if type_name == "aws elastic ip":
        region = diag_item_detail(diag, "region")
        suffix = f" in {region}" if region else ""
        return f"  [Renew] Releases and allocates a new AWS Elastic IP{suffix}"
    if type_name == "aliyun elastic ip":
        return "  [Renew] Releases and allocates a new Aliyun Elastic IP"
    return f"  [Renew] Reallocates {type_name}"
