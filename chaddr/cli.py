"""Command-line interface for chaddr."""

from __future__ import annotations

import argparse
import logging

from chaddr.address import AddressSet, is_ipv4, parse_address_set
from chaddr.config import load_config, resolve_client_ip
from chaddr.gui.app import run_gui
from chaddr.orchestrator import apply_address_profile, diagnose_profile, reallocate_profile
from chaddr.profile import Profile, ensure_profile_dir, list_profiles, load_profile
from chaddr.proxy import apply_proxy_env, log_proxy_hint, restore_proxy_env


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
        description="Change or reallocate IP addresses defined in profile files.",
    )
    parser.add_argument(
        "profiles",
        nargs="*",
        help="Profile name(s) under ~/.config/chaddr/profile/ (override with CHADDR_PROFILE_DIR)",
    )
    parser.add_argument(
        "-c",
        "--config",
        metavar="FILE",
        help="JSON config file with API keys/secrets (default: ./chaddr.conf or ~/.config/chaddr/chaddr.conf)",
    )
    parser.add_argument(
        "--proxy",
        help="Proxy URL, e.g. socks5://127.0.0.1:1080 or http://127.0.0.1:8080",
    )
    parser.add_argument("--diagnose", action="store_true", help="Run diagnosis only (CLI mode)")
    parser.add_argument("--refetch", action="store_true", help="Reallocate elastic IP and propagate (CLI mode)")
    parser.add_argument("--apply", metavar="IP", help="Manually apply IPv4/IPv6 to profile (CLI mode)")
    parser.add_argument("--apply-ipv4", metavar="IP", help="New IPv4 for manual apply")
    parser.add_argument("--apply-ipv6", metavar="IP", help="New IPv6 for manual apply")
    parser.add_argument("--old-ip", help="Old IP for manual apply when auto-detection fails")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase logging verbosity")
    parser.add_argument("--no-gui", action="store_true", help="Force CLI mode even without action flags")
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
    refetch: bool,
    apply_ip: str | None,
    apply_ipv4: str | None,
    apply_ipv6: str | None,
    old_ip: str | None,
) -> int:
    exit_code = 0
    for name in profiles:
        profile = load_profile(name)
        spare_extra = _spare_from_sets_for_profile(profile, old_ip)
        if diagnose:
            result = diagnose_profile(
                profile,
                cli_options,
                proxy,
                logger,
                spare_from_sets=spare_extra,
            )
            logger.info("Profile %s: %s", name, result.message)
            for diag in result.diagnose_results:
                status = "OK" if diag.ok else "FAIL"
                print(f"[{status}] {diag.type_name}: {diag.summary}")
                for item in diag.items:
                    mark = "OK" if item.ok else "FAIL"
                    print(f"  [{mark}] {item.label}: {item.detail}")
                    if not item.ok and item.guidance:
                        print(f"        -> {item.guidance}")
            if not result.ok:
                exit_code = 1
        elif refetch:
            result = reallocate_profile(profile, None, cli_options, proxy, logger)
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
            logger.error("No CLI action specified; use --diagnose, --refetch, or --apply")
            return 2
    return exit_code


def _merge_options(config_options: dict, cli_options: dict) -> dict:
    merged = dict(config_options)
    merged.update(cli_options)
    return merged


def main(argv: list[str] | None = None) -> int:
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

    resolve_client_ip(cli_options, proxy, config_path, logger)
    ensure_profile_dir()

    try:
        profiles = args.profiles
        cli_mode = (
            args.no_gui
            or args.diagnose
            or args.refetch
            or args.apply is not None
            or args.apply_ipv4
            or args.apply_ipv6
        )

        if args.old_ip:
            cli_options["old_ip"] = args.old_ip

        if cli_mode:
            if not profiles:
                available = list_profiles()
                parser.error(f"profile required for CLI mode; available: {', '.join(available) or '(none)'}")
            return _run_cli(
                profiles,
                cli_options,
                proxy,
                logger,
                args.diagnose,
                args.refetch,
                args.apply,
                args.apply_ipv4,
                args.apply_ipv6,
                args.old_ip,
            )

        run_gui(profiles, cli_options, proxy, config_path, old_ip=args.old_ip)
        return 0
    finally:
        restore_proxy_env(proxy_backup)
