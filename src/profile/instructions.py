"""Profile instruction listing helpers for the GUI."""

from __future__ import annotations

from chaddr.profile.instance import canonical_from_type_label
from chaddr.profile.model import (
    Profile,
    ProfileEntry,
    ProfileFromBlock,
    ProfileInstruction,
    profile_option_truthy,
)
from chaddr.profile_lexer import canonical_ws_tokens

_TYPE_SUMMARY_LABELS = {
    "hosts file": "hosts",
    "zone file": "zone",
    "bind db": "zone",
    "registered nameserver": "nameserver",
    "aws elastic ip": "aws elastic ip",
    "aliyun elastic ip": "aliyun elastic ip",
    "file": "file",
    "changelog": "changelog",
    "router": "router",
}


def _shorten_path(path: str, max_len: int = 36) -> str:
    text = path.strip()
    if len(text) <= max_len:
        return text
    return "..." + text[-(max_len - 3) :]


def _instruction_detail(type_name: str, options: dict[str, str], *, kind: str) -> str:
    lower = canonical_ws_tokens(type_name).lower()
    if kind == "from":
        if lower == "resolve":
            return (
                options.get("resolve")
                or options.get("host")
                or options.get("name")
                or ""
            )
        if lower.endswith(" nic"):
            return (
                options.get("nic")
                or options.get("eni")
                or options.get("network_interface")
                or options.get("network_interface_id")
                or ""
            )
        if lower in ("ec2", "aliyun") or lower.endswith(" instance") or "instance" in lower:
            return options.get("instance") or options.get("instance_id") or ""
        return next(iter(options.values()), "") if options else ""

    if lower == "registered nameserver":
        ns = options.get("ns") or options.get("host") or ""
        hosts = [part.strip() for part in ns.split(",") if part.strip()]
        if hosts:
            return ", ".join(hosts)
        return options.get("domain") or options.get("ns_domain") or options.get("api") or ""

    if lower == "router":
        parts = [
            options.get("system") or "",
            options.get("config") or "",
            options.get("host") or options.get("gateway") or "",
        ]
        return " ".join(part for part in parts if part).strip()

    for key in ("path", "changelog", "zone", "host", "region", "api"):
        value = options.get(key)
        if value:
            if key in ("path", "changelog", "zone"):
                return _shorten_path(value)
            return value
    return ""


def _format_instruction_summary(kind: str, type_name: str, options: dict[str, str]) -> str:
    detail = _instruction_detail(type_name, options, kind=kind)
    if kind == "from":
        label = canonical_from_type_label(type_name)
        return f"from {label} {detail}".rstrip()
    label = _TYPE_SUMMARY_LABELS.get(canonical_ws_tokens(type_name).lower(), type_name)
    summary = f"{label} {detail}".rstrip()
    if profile_option_truthy(options.get("optional")):
        summary = f"{summary} (optional)"
    return summary


def list_profile_instructions(profile: Profile) -> list[ProfileInstruction]:
    """Return profile AST instructions (from/type) in file order for the GUI."""
    from chaddr.profile.parse import _parse_profile, profile_from_blocks

    instructions: list[ProfileInstruction] = []
    from_index = 0
    entry_index = 0

    def add_from(block: ProfileFromBlock) -> None:
        nonlocal from_index
        instructions.append(
            ProfileInstruction(
                key=f"from:{from_index}",
                kind="from",
                type_name=block.from_type,
                summary=_format_instruction_summary("from", block.from_type, block.options),
                from_index=from_index,
            )
        )
        from_index += 1

    def add_type(entry: ProfileEntry) -> None:
        nonlocal entry_index
        optional = profile_option_truthy(entry.options.get("optional"))
        summary = _format_instruction_summary("type", entry.type, entry.options)
        instructions.append(
            ProfileInstruction(
                key=f"type:{entry_index}",
                kind="type",
                type_name=entry.type,
                summary=summary,
                entry_index=entry_index,
                optional=optional,
            )
        )
        entry_index += 1

    if profile.path is not None and profile.path.is_file() and profile.from_blocks is None:
        # Preserve file order by walking the parser stream.
        for item in _parse_profile(profile.path.read_text(encoding="utf-8"), []):
            if isinstance(item, ProfileFromBlock):
                add_from(item)
            elif isinstance(item, ProfileEntry):
                add_type(item)
        return instructions

    for block in profile_from_blocks(profile):
        add_from(block)
    for entry in profile.entries:
        add_type(entry)
    return instructions
