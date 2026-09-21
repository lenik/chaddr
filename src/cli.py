"""Command-line interface for chaddr."""

from __future__ import annotations

import argparse
import logging
import os
import sys

if sys.platform.startswith("linux") and "GTK_A11Y" not in os.environ:
    os.environ["GTK_A11Y"] = "none"

from chaddr.address import AddressSet, is_ipv4, is_ipv6, parse_address_set
from chaddr.config import load_config, resolve_client_ip
from chaddr.gui.app import run_gui
from chaddr.i18n import _, init_i18n
from chaddr.orchestrator import apply_address_profile, diagnose_profile, reallocate_profile
from chaddr.profile import Profile, ensure_profile_dir, list_profiles, list_profile_candidate_addresses, load_profile
from chaddr.proxy import apply_proxy_env, log_proxy_hint, restore_proxy_env
from chaddr.types import get_handler_class


def _profile_requires_public_ip(profile: Profile) -> bool:
    for entry in profile.entries:
        handler_cls = get_handler_class(entry.type)
        if handler_cls and getattr(handler_cls, "requires_public_ip", False):
            return True
    return False


def _parse_option_flags(unknown: list[str]) -> dict:
    options: dict[str, str] = {}
    index = 0
    while index < len(unknown):
        token = unknown[index]
        if not token.startswith("--"):
            index += 1
            continue
        key = token.lstrip("-").replace("-", "_")
        if index + 1 < len(unknown) and not unknown[index + 1].startswith("--"):
            options[key] = unknown[index + 1]
            index += 2
        else:
            options[key] = "1"
            index += 1
    return options


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chaddr",
        description=_("Change or reallocate IP addresses defined in profile files."),
    )
    parser.add_argument(
        "profiles",
        nargs="*",
        help=_("Profile name(s) under ~/.config/chaddr/profile/ (override with CHADDR_PROFILE_DIR)"),
    )
    parser.add_argument(
        "-c",
        "--config",
        metavar=_("FILE"),
        help=_("JSON config file with API keys/secrets (default: ./chaddr.conf or ~/.config/chaddr/chaddr.conf)"),
    )
    parser.add_argument(
        "--proxy",
        help=_("Proxy URL, e.g. socks5://127.0.0.1:1080 or http://127.0.0.1:8080"),
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help=_("Run diagnosis only (CLI mode); with --apply IP, that IP is the diagnose target"),
    )
    parser.add_argument(
        "-A",
        "--addresses",
        action="store_true",
        help=_("List all candidate addresses for profile(s) (CLI mode)"),
    )
    parser.add_argument(
        "--renew",
        action="store_true",
        help=_("Reallocate elastic IP and propagate (CLI mode)"),
    )
    parser.add_argument(
        "--apply",
        metavar=_("IP"),
        help=_("Manually apply IPv4/IPv6 to profile (CLI mode)"),
    )
    parser.add_argument(
        "--apply-ipv4",
        metavar=_("IP"),
        help=_("New IPv4 for manual apply"),
    )
    parser.add_argument(
        "--apply-ipv6",
        metavar=_("IP"),
        help=_("New IPv6 for manual apply"),
    )
    parser.add_argument(
        "--old-ip",
        help=_("Old IP for manual apply when auto-detection fails"),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help=_("Increase logging verbosity"),
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help=_("Force CLI mode even without action flags"),
    )
    return parser


def _setup_logging(verbose: int) -> logging.Logger:
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")
    return logging.getLogger("chaddr")


def _spare_from_old_ip(old_ip: str | None) -> list[AddressSet]:
    if not old_ip:
        return []
    if is_ipv4(old_ip):
        return [AddressSet(ipv4=old_ip)]
    return [AddressSet(ipv6=old_ip)]


def _spare_from_sets_for_profile(profile: Profile, old_ip: str | None) -> list[AddressSet]:
    sets = list(profile.addr_history_sets())
    sets.extend(_spare_from_old_ip(old_ip))
    return sets


def _run_cli(
    profiles: list[str],
    cli_options: dict,
    proxy: str | None,
    logger: logging.Logger,
    diagnose: bool,
    addresses: bool,
    renew: bool,
    apply_ip: str | None,
    apply_ipv4: str | None,
    apply_ipv6: str | None,
    old_ip: str | None,
) -> int:
    exit_code = 0
    for name in profiles:
        profile = load_profile(name)
        if _profile_requires_public_ip(profile) and not (cli_options.get("client_ip") or "").strip():
            logger.error(
                "Profile %s requires a public IP (client_ip), but none could be determined",
                name,
            )
            exit_code = 1
            continue
        if addresses:
            for entry in list_profile_candidate_addresses(profile, cli_options, proxy, logger):
                print(entry.display())
            continue
        spare_extra = _spare_from_sets_for_profile(profile, old_ip)
        if diagnose:
            target = None
            try:
                if apply_ip or apply_ipv4 or apply_ipv6:
                    ipv4 = apply_ipv4 or (apply_ip if apply_ip and is_ipv4(apply_ip) else None)
                    ipv6 = apply_ipv6 or (apply_ip if apply_ip and is_ipv6(apply_ip) else None)
                    if ipv4 or ipv6:
                        target = parse_address_set(ipv4, ipv6)
            except ValueError as exc:
                logger.error("%s", exc)
                exit_code = 1
                continue
            result = diagnose_profile(
                profile,
                cli_options,
                proxy,
                logger,
                spare_from_sets=spare_extra,
                target_addresses=target,
            )
            if result.new_addresses and not result.new_addresses.is_empty():
                print(f"Target: {result.new_addresses.format()}")
            if result.source_addresses and not result.source_addresses.is_empty():
                print(f"From-source: {result.source_addresses.format()}")
            logger.info("Profile %s: %s", name, result.message)
            for diag in result.diagnose_results:
                status = "OK" if diag.ok else "FAIL"
                print(f"[{status}] {diag.type_name}: {diag.summary}")
                for item in diag.items:
                    mark = "OK" if item.ok else "FAIL"
                    print(f"  [{mark}] {item.label}: {item.detail}")
                    if not item.ok and item.guidance:
                        print(f"        -> {item.guidance}")
            print(f"Result: {result.message}")
            if not result.ok:
                exit_code = 1
        elif renew:
            result = reallocate_profile(
                profile,
                cli_options,
                proxy,
                logger,
                spare_from_sets=spare_extra,
            )
            if result.ok:
                logger.info("Profile %s: %s", name, result.message)
            else:
                logger.error("Profile %s: %s", name, result.message)
                exit_code = 1
        elif apply_ip or apply_ipv4 or apply_ipv6:
            try:
                if apply_ip and not apply_ipv4:
                    apply_ipv4 = apply_ip
                new_addresses = parse_address_set(apply_ipv4, apply_ipv6)
            except ValueError as exc:
                logger.error("%s", exc)
                exit_code = 1
                continue
            result = apply_address_profile(
                profile,
                new_addresses,
                cli_options,
                proxy,
                logger,
                spare_from_sets=spare_extra,
            )
            if result.ok:
                logger.info("Profile %s: %s", name, result.message)
            else:
                logger.error("Profile %s: %s", name, result.message)
                exit_code = 1
        else:
            logger.error(_("No CLI action specified; use --diagnose, --renew, or --apply"))
            return 2
    return exit_code


def _merge_options(config_options: dict, cli_options: dict) -> dict:
    merged = dict(config_options)
    merged.update(cli_options)
    return merged


def main(argv: list[str] | None = None) -> int:
    init_i18n(sys.argv[0] if argv is None else (argv[0] if argv else sys.argv[0]))
    parser = _build_parser()
    args, unknown = parser.parse_known_args(argv)
    flag_options = _parse_option_flags(unknown)
    logger = _setup_logging(args.verbose)

    try:
        config_options, config_proxy, config_path = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    cli_options = _merge_options(config_options, flag_options)
    proxy = args.proxy or config_proxy

    if config_path:
        logger.info("Config: %s", config_path)

    proxy_backup = apply_proxy_env(proxy)
    logger.info(log_proxy_hint(proxy))

    ensure_profile_dir()

    try:
        profiles = args.profiles
        cli_mode = (
            args.no_gui
            or args.diagnose
            or args.addresses
            or args.renew
            or args.apply is not None
            or args.apply_ipv4
            or args.apply_ipv6
        )

        if args.old_ip:
            cli_options["old_ip"] = args.old_ip

        if cli_mode:
            # CLI needs client_ip up front; GUI resolves asynchronously with a progress bar.
            resolve_client_ip(cli_options, proxy, config_path, logger)
            if not profiles:
                available = list_profiles()
                parser.error(f"profile required for CLI mode; available: {', '.join(available) or '(none)'}")
            return _run_cli(
                profiles,
                cli_options,
                proxy,
                logger,
                args.diagnose,
                args.addresses,
                args.renew,
                args.apply,
                args.apply_ipv4,
                args.apply_ipv6,
                args.old_ip,
            )

        run_gui(profiles, cli_options, proxy, config_path, old_ip=args.old_ip)
        return 0
    finally:
        restore_proxy_env(proxy_backup)
