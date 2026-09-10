"""Cloud / instance from-block helpers."""

from __future__ import annotations

import logging

from chaddr.address import AddressSet, is_ipv4, is_ipv6
from chaddr.profile.model import Profile, ProfileFromBlock
from chaddr.profile_lexer import canonical_ws_tokens

INSTANCE_FROM_HANDLERS: dict[str, str] = {
    "ec2": "aws elastic ip",
    "ec2 instance": "aws elastic ip",
    "ec2 nic": "aws elastic ip",
    "aliyun": "aliyun elastic ip",
    "aliyun instance": "aliyun elastic ip",
    "aliyun nic": "aliyun elastic ip",
}

# Canonical GUI / address-panel source labels for cloud from: blocks.
FROM_TYPE_SOURCE_LABELS: dict[str, str] = {
    "ec2": "ec2",
    "ec2 instance": "ec2",
    "ec2 nic": "ec2 nic",
    "aliyun": "aliyun",
    "aliyun instance": "aliyun",
    "aliyun nic": "aliyun nic",
}


def is_instance_from_type(from_type: str) -> bool:
    return canonical_ws_tokens(from_type).lower() in INSTANCE_FROM_HANDLERS


def canonical_from_type_label(from_type: str) -> str:
    """Normalize profile from: type to GUI source label (ec2 / ec2 nic / …)."""
    label = canonical_ws_tokens(from_type).lower()
    return FROM_TYPE_SOURCE_LABELS.get(label, label)


def cloud_from_block_options(profile: Profile) -> dict[str, str]:
    """Merge identity options from cloud from: blocks into elastic-IP handler config."""
    from chaddr.profile.parse import profile_from_blocks

    merged: dict[str, str] = {}
    for from_block in profile_from_blocks(profile):
        from_type = canonical_ws_tokens(from_block.from_type).lower()
        if from_type not in INSTANCE_FROM_HANDLERS:
            continue
        merged.update(from_block.options)
        merged["from_type"] = canonical_from_type_label(from_block.from_type)
    return merged


def ec2_from_block_options(profile: Profile) -> dict[str, str]:
    """Backward-compatible alias for cloud_from_block_options (AWS + Aliyun)."""
    return cloud_from_block_options(profile)


def handler_config_for_instance_from(
    profile: Profile,
    from_block: ProfileFromBlock,
) -> tuple[str, dict[str, str]] | None:
    handler_type = INSTANCE_FROM_HANDLERS.get(canonical_ws_tokens(from_block.from_type).lower())
    if handler_type is None:
        return None
    config: dict[str, str] = {}
    for entry in profile.entries:
        if canonical_ws_tokens(entry.type).lower() == handler_type:
            config.update(entry.config)
    config.update(from_block.options)
    config["from_type"] = canonical_from_type_label(from_block.from_type)
    return handler_type, config


def instance_from_block_addresses(
    profile: Profile,
    from_block: ProfileFromBlock,
    cli_options: dict | None = None,
    proxy: str | None = None,
    logger: logging.Logger | None = None,
) -> AddressSet:
    from chaddr.profile.parse import merge_cli_options
    from chaddr.types import create_handler

    spec = handler_config_for_instance_from(profile, from_block)
    if spec is None:
        return AddressSet()
    handler_type, config = spec
    log = logger or logging.getLogger("chaddr")
    merged_options = merge_cli_options(profile, cli_options or {})
    handler = create_handler(handler_type, config, merged_options, proxy, log)
    diag = handler.diagnose()
    if not diag.addresses:
        failed = next((item for item in diag.items if not item.ok), None)
        if failed:
            raise RuntimeError(f"{failed.label}: {failed.detail}")
    ipv4 = next((ip for ip in diag.addresses if is_ipv4(ip)), None)
    ipv6 = next((ip for ip in diag.addresses if is_ipv6(ip)), None)
    return AddressSet(ipv4=ipv4, ipv6=ipv6)


def instance_profile_addresses(
    profile: Profile,
    cli_options: dict | None = None,
    proxy: str | None = None,
    logger: logging.Logger | None = None,
) -> AddressSet:
    from chaddr.profile.parse import profile_from_blocks

    ipv4: str | None = None
    ipv6: str | None = None
    for from_block in profile_from_blocks(profile):
        if not is_instance_from_type(from_block.from_type):
            continue
        resolved = instance_from_block_addresses(profile, from_block, cli_options, proxy, logger)
        if resolved.ipv4:
            ipv4 = resolved.ipv4
        if resolved.ipv6:
            ipv6 = resolved.ipv6
    return AddressSet(ipv4=ipv4, ipv6=ipv6)


def resolve_profile_addresses(profile: Profile) -> AddressSet:
    from chaddr.address import AddressSet, resolve_from
    from chaddr.profile.parse import profile_from_blocks

    ipv4: str | None = None
    ipv6: str | None = None
    for from_block in profile_from_blocks(profile):
        if canonical_ws_tokens(from_block.from_type).lower() != "resolve":
            continue
        resolved = resolve_from("resolve", from_block.options)
        if resolved.ipv4:
            ipv4 = resolved.ipv4
        if resolved.ipv6:
            ipv6 = resolved.ipv6
    return AddressSet(ipv4=ipv4, ipv6=ipv6)
