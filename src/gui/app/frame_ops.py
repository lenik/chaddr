"""Async diagnose/renew/apply operations and spare-address helpers."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import wx

from chaddr.address import AddressEntry, AddressSet, is_ipv4, is_ipv6, merge_address_entries, unique_spare_sets, spare_sets_from_entries
from chaddr.config import save_config
from chaddr.orchestrator import (
    ProfileRunResult,
    apply_address_profile,
    diagnose_profile,
    reallocate_profile,
)
from chaddr.profile import ProfileAddressFetchEvent, iter_profile_address_fetch, load_profile
from chaddr.proxy import apply_proxy_env, restore_proxy_env
from chaddr.types.base import DiagnoseResult
from chaddr.types import get_handler_class

from .diagnose_text import _format_diagnose_result, _log_diagnose
from .ids import ID_APPLY, ID_DIAGNOSE, ID_RENEW, STATUS_BUSY, STATUS_FAIL, STATUS_OK
from .log_support import AggregateProgress, ProfileLogContext


class OperationsMixin:
    def _profile_history_entries(self, profile_name: str) -> list[AddressEntry]:
        profile = load_profile(profile_name)
        entries: list[AddressEntry] = []
        for record in profile.addr_history_records():
            if is_ipv4(record.address) or is_ipv6(record.address):
                entries.append(AddressEntry.from_history_ip(record.address, record.timestamp))
        return entries

    def _seed_old_ip(self) -> None:
        old_ip = (self._old_ip or "").strip()
        if not old_ip:
            return
        if not is_ipv4(old_ip) and not is_ipv6(old_ip):
            self.logger.warning("Ignoring invalid --old-ip: %s", old_ip)
            return
        entries = list(self.address_panel.get_entries())
        if any(entry.address == old_ip for entry in entries):
            return
        entries.append(AddressEntry.from_old_ip(old_ip))
        self.address_panel.set_entries(entries)

    def _seed_history(self) -> None:
        selected = self._selected_profiles()
        if len(selected) != 1:
            return
        entries = merge_address_entries(
            self.address_panel.get_entries(),
            self._profile_history_entries(selected[0]),
        )
        self.address_panel.set_entries(entries)

    def _add_instance_entries(
        self,
        ips: list[str],
        *,
        source: str = "ec2",
        detail: str = "",
    ) -> None:
        incoming = [
            AddressEntry.from_instance_ip(ip, detail, source=source)
            for ip in ips
            if is_ipv4(ip) or is_ipv6(ip)
        ]
        if not incoming:
            return
        self.address_panel.set_entries(
            merge_address_entries(
                self.address_panel.get_entries(),
                incoming,
                replace_sources=frozenset({source}),
            ),
        )

    def _profile_spare_sets(self, profile_name: str) -> list[AddressSet]:
        """Local-only spare snapshot for the worker (no DNS/cloud — avoids GUI freeze)."""
        profile = load_profile(profile_name)
        sets: list[AddressSet] = list(profile.addr_history_sets())
        old_ip = self.cli_options.get("old_ip") or self._old_ip
        if old_ip:
            if is_ipv4(old_ip):
                sets.append(AddressSet(ipv4=old_ip))
            elif is_ipv6(old_ip):
                sets.append(AddressSet(ipv6=old_ip))
        sets.extend(spare_sets_from_entries(self.address_panel.get_entries()))
        return unique_spare_sets(sets)

    def _snapshot_spare_from_sets(self, profiles: list[str]) -> dict[str, list[AddressSet]]:
        """Capture per-profile spare sets on the GUI thread before background work."""
        return {name: self._profile_spare_sets(name) for name in profiles}

    def _refresh_action_buttons(self) -> None:
        selected = self._selected_profiles()
        if not selected:
            self.apply_btn.Enable(False)
            self.renew_btn.Enable(False)
            return

        has_manual = False
        has_reallocate = False
        for name in selected:
            try:
                profile = self._load_session_profile(name)
            except Exception:
                profile = load_profile(name)
            has_manual = has_manual or profile.has_manual_types()
            for entry in profile.entries:
                handler_cls = get_handler_class(entry.type)
                if handler_cls and handler_cls.supports_reallocate:
                    has_reallocate = True

        self.renew_btn.Enable(has_reallocate)
        self.apply_btn.Enable(
            len(selected) == 1
            and has_manual
            and self.address_panel.has_valid_apply_selection()
        )

    def _refresh_addresses_from_profile(self) -> None:
        selected = self._selected_profiles()
        if len(selected) != 1:
            return

        profile_name = selected[0]
        self._operation_cancel_event.clear()
        self._address_fetch_token += 1
        token = self._address_fetch_token
        manual_entries = [
            entry for entry in self.address_panel.get_entries() if entry.source == "manual"
        ]

        wx.CallAfter(self._begin_address_fetch, token, manual_entries)

        def worker() -> None:
            try:
                profile = load_profile(profile_name)
                for event in iter_profile_address_fetch(
                    profile,
                    self.cli_options,
                    self.proxy,
                    self.logger,
                ):
                    if token != self._address_fetch_token:
                        return
                    wx.CallAfter(self._on_address_fetch_step, token, event)
            except Exception as exc:
                self.logger.warning("Address fetch failed for %s: %s", profile_name, exc)
            finally:
                wx.CallAfter(self._finish_address_fetch, token)

        self._address_fetch_thread = threading.Thread(target=worker, daemon=True, name="address-fetch")
        self._address_fetch_thread.start()

    def _begin_address_fetch(self, token: int, manual_entries: list[AddressEntry]) -> None:
        if token != self._address_fetch_token:
            return
        self.address_panel.set_entries(manual_entries)
        if not self._public_ip_progress_active:
            self._show_progress()
            self._progress.SetValue(0)
            self._set_action("Loading addresses...")

    def _on_address_fetch_step(self, token: int, event: ProfileAddressFetchEvent) -> None:
        if token != self._address_fetch_token:
            return
        self.address_panel.merge_entries(event.entries, replace_sources=event.replace_sources)
        if not self._public_ip_progress_active:
            self._progress.SetValue(max(0, min(100, int(event.fraction * 100))))
            self._set_action(event.message)
        self._refresh_action_buttons()

    def _finish_address_fetch(self, token: int) -> None:
        if token != self._address_fetch_token:
            return
        if self._operation_cancel_event.is_set():
            if not self._public_ip_progress_active:
                self._hide_progress()
            self._set_action("Cancelled")
            self._operation_cancel_event.clear()
            self._refresh_action_buttons()
            return
        if not (self._worker and self._worker.is_alive()) and not self._public_ip_progress_active:
            self._progress.SetValue(100)
            self._hide_progress()
            self._set_action("Ready")
        self._refresh_action_buttons()

    def _set_busy(self, busy: bool) -> None:
        menu = self.GetMenuBar()
        for item_id in (ID_DIAGNOSE, ID_RENEW, ID_APPLY):
            menu.Enable(item_id, not busy)
        self.diagnose_btn.Enable(not busy)
        if not busy:
            self._refresh_manual_mode()
        else:
            self.renew_btn.Enable(False)
            self.apply_btn.Enable(False)

    def _update_progress(self, fraction: float, message: str) -> None:
        def _apply() -> None:
            if self._public_ip_progress_active:
                return
            self._progress.SetValue(int(fraction * 100))
            self._set_action(message)

        wx.CallAfter(_apply)

    def _profiles_require_public_ip(self, profiles: list[str]) -> bool:
        for name in profiles:
            try:
                profile = self._load_session_profile(name)
            except Exception:
                profile = load_profile(name)
            for entry in profile.entries:
                handler_cls = get_handler_class(entry.type)
                if handler_cls and getattr(handler_cls, "requires_public_ip", False):
                    return True
        return False

    def _ensure_public_ip_ready(self, profiles: list[str]) -> bool:
        """Block ops that need a public IP when none is available yet / at all."""
        if not self._profiles_require_public_ip(profiles):
            return True
        if self._remote_ip_value():
            return True
        if self._public_ip_loading:
            wx.MessageBox(
                "Still determining this host's public IP.\n"
                "Wait for the status-bar progress to finish, then try again.",
                "Public IP required",
                wx.OK | wx.ICON_INFORMATION,
            )
            return False
        wx.MessageBox(
            "No public IP is available for this host.\n"
            "Namecheap and similar APIs require a public client IP.",
            "Public IP required",
            wx.OK | wx.ICON_WARNING,
        )
        return False

    def _run_async(self, label: str, func) -> None:
        if self._worker and self._worker.is_alive():
            wx.MessageBox("An operation is already running.", "Busy", wx.OK | wx.ICON_WARNING)
            return

        profiles = self._selected_profiles()
        if not profiles:
            wx.MessageBox("Select a profile.", "No profile", wx.OK | wx.ICON_INFORMATION)
            return

        if not self._ensure_public_ip_ready(profiles):
            return

        self._warning_count = 0
        self._error_count = 0
        self._warning_messages.clear()
        self._error_messages.clear()
        self._operation_cancel_event.clear()
        self._set_busy(True)
        wx.CallAfter(self._show_progress)
        self._set_action(f"Running {label}...")
        if label == "diagnose":
            self._prepare_diagnose_output(profiles)
            for name in profiles:
                self._set_profile_status(name, STATUS_BUSY)
        elif label in ("renew", "apply"):
            self._prepare_log_output(profiles)
            if profiles:
                self._activate_profile_output_tabs(profiles[0])
            for name in profiles:
                self._set_profile_status(name, STATUS_BUSY)
        else:
            wx.CallAfter(lambda: self._prepare_output_panes(diagnostics=False, logging=True))
        for name in profiles:
            with ProfileLogContext(name):
                self.logger.info("=== %s: %s ===", label, name)

        spare_by_profile = self._snapshot_spare_from_sets(profiles)
        session_profiles = {name: self._load_session_profile(name) for name in profiles}
        target_addresses = None
        if label == "diagnose":
            try:
                target_addresses = self.address_panel.get_apply_address_set()
            except ValueError:
                target_addresses = None

        def worker() -> None:
            try:
                if label == "diagnose":
                    func(profiles, spare_by_profile, session_profiles, target_addresses)
                else:
                    func(profiles, spare_by_profile, session_profiles)
            except Exception as exc:
                self.logger.exception("Operation failed: %s", exc)
                wx.CallAfter(wx.MessageBox, str(exc), "Error", wx.OK | wx.ICON_ERROR)
            finally:
                cancelled = self._operation_cancel_event.is_set()
                wx.CallAfter(self._finish_operation, "Cancelled" if cancelled else "Done")

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _finish_operation(self, label: str = "Done") -> None:
        cancelled = label == "Cancelled"
        self._operation_cancel_event.clear()
        self._set_busy(False)
        self._progress.SetValue(100 if not cancelled else 0)
        self._hide_progress()
        self._set_action(label)
        if cancelled:
            self.logger.info("Operation cancelled")

    def _do_diagnose(
        self,
        profiles: list[str],
        spare_by_profile: dict[str, list[AddressSet]],
        session_profiles: dict | None = None,
        target_addresses: AddressSet | None = None,
    ) -> None:
        if self._operation_cancel_event.is_set():
            return
        aggregate = AggregateProgress(self._update_progress, profiles)
        results: dict[str, ProfileRunResult] = {}
        session_profiles = session_profiles or {}

        def diagnose_one(name: str) -> ProfileRunResult | None:
            if self._operation_cancel_event.is_set():
                return None
            wx.CallAfter(self._activate_profile_output_tabs, name)
            wx.CallAfter(self._append_profile_summary, name, f"=== Profile: {name} ===\n")
            if target_addresses is not None and not target_addresses.is_empty():
                wx.CallAfter(
                    self._append_profile_summary,
                    name,
                    f"Target: {target_addresses.format()}\n",
                )
            profile = session_profiles.get(name) or load_profile(name)

            def on_result(diag: DiagnoseResult, profile_name: str = name) -> None:
                if self._operation_cancel_event.is_set():
                    return
                text = _format_diagnose_result(diag) + "\n"
                wx.CallAfter(
                    lambda pn=profile_name, content=text: self._append_profile_summary(pn, content),
                )

            try:
                with ProfileLogContext(name):
                    result = diagnose_profile(
                        profile,
                        self.cli_options,
                        self.proxy,
                        self.logger,
                        aggregate.callback(name),
                        spare_from_sets=spare_by_profile.get(name, []),
                        on_result=on_result,
                        target_addresses=target_addresses,
                    )
            except Exception as exc:
                with ProfileLogContext(name):
                    self.logger.exception("Diagnose failed for %s: %s", name, exc)
                result = ProfileRunResult(name, False, str(exc))

            if self._operation_cancel_event.is_set():
                return result
            wx.CallAfter(self._append_profile_summary, name, f"Result: {result.message}\n\n")
            wx.CallAfter(
                self._set_profile_status,
                name,
                STATUS_OK if result.ok else STATUS_FAIL,
            )
            return result

        with ThreadPoolExecutor(max_workers=max(1, len(profiles))) as executor:
            futures = {}
            for name in profiles:
                if self._operation_cancel_event.is_set():
                    break
                futures[executor.submit(diagnose_one, name)] = name
            for future in as_completed(futures):
                if self._operation_cancel_event.is_set():
                    break
                name = futures[future]
                result = future.result()
                if result is not None:
                    results[name] = result

        if not self._operation_cancel_event.is_set():
            wx.CallAfter(self._apply_diagnose_batch_results, profiles, results)

    def _apply_diagnose_batch_results(
        self,
        profiles: list[str],
        results: dict[str, ProfileRunResult],
    ) -> None:
        all_ok = True
        for name in profiles:
            result = results.get(name)
            if result is None:
                continue
            with ProfileLogContext(name):
                _log_diagnose(self.logger, result)
            all_ok = all_ok and result.ok
        summary = "All profiles OK" if all_ok else "Some profiles have issues"
        self.logger.info("=== Diagnose summary: %s ===", summary)

    def _do_renew(
        self,
        profiles: list[str],
        spare_by_profile: dict[str, list[AddressSet]],
        session_profiles: dict | None = None,
    ) -> None:
        aggregate = AggregateProgress(self._update_progress, profiles)
        session_profiles = session_profiles or {}

        for name in profiles:
            if self._operation_cancel_event.is_set():
                break
            wx.CallAfter(self._activate_profile_output_tabs, name)
            profile = session_profiles.get(name) or load_profile(name)
            try:
                with ProfileLogContext(name):
                    result = reallocate_profile(
                        profile,
                        self.cli_options,
                        self.proxy,
                        self.logger,
                        aggregate.callback(name),
                        spare_from_sets=spare_by_profile.get(name, []),
                    )
            except Exception as exc:
                with ProfileLogContext(name):
                    self.logger.exception("Renew failed for %s: %s", name, exc)
                wx.CallAfter(self._set_profile_status, name, STATUS_FAIL)
                continue

            if result.ok:
                with ProfileLogContext(name):
                    self.logger.info("Profile %s: %s", name, result.message)
                wx.CallAfter(self._set_profile_status, name, STATUS_OK)
                if result.new_addresses and not result.new_addresses.is_empty():
                    wx.CallAfter(
                        self._add_instance_entries,
                        result.new_addresses.all(),
                    )
                    wx.CallAfter(self._seed_history)
                    wx.CallAfter(self._refresh_history_from_profile)
            else:
                with ProfileLogContext(name):
                    self.logger.error("Profile %s failed: %s", name, result.message)
                wx.CallAfter(self._set_profile_status, name, STATUS_FAIL)

    def _do_apply(
        self,
        profiles: list[str],
        spare_by_profile: dict[str, list[AddressSet]],
        session_profiles: dict | None = None,
    ) -> None:
        if len(profiles) != 1:
            wx.CallAfter(
                wx.MessageBox,
                "Select exactly one profile for address change.",
                "Profile",
                wx.OK | wx.ICON_WARNING,
            )
            return

        try:
            new_addresses = self.address_panel.get_apply_address_set()
        except ValueError as exc:
            wx.CallAfter(wx.MessageBox, str(exc), "Invalid selection", wx.OK | wx.ICON_WARNING)
            return

        name = profiles[0]
        if self._operation_cancel_event.is_set():
            return
        wx.CallAfter(self._activate_profile_output_tabs, name)
        session_profiles = session_profiles or {}
        profile = session_profiles.get(name) or load_profile(name)

        def progress(fraction: float, message: str) -> None:
            self._update_progress(fraction, message)

        with ProfileLogContext(name):
            spare_sets = spare_by_profile.get(name, [])
            self.logger.info(
                "Apply %s: selected %s (%d unique spare address(es))",
                name,
                new_addresses.format(),
                len(spare_sets),
            )
            for index, addr_set in enumerate(spare_sets, start=1):
                if not addr_set.is_empty():
                    self.logger.info("  spare[%d]: %s", index, addr_set.format())
            result = apply_address_profile(
                profile,
                new_addresses,
                self.cli_options,
                self.proxy,
                self.logger,
                progress,
                spare_from_sets=spare_by_profile.get(name, []),
            )
        if result.ok:
            wx.CallAfter(self._set_profile_status, name, STATUS_OK)
            with ProfileLogContext(name):
                self.logger.info("Profile %s: %s", name, result.message)
                if result.old_addresses and result.new_addresses:
                    self.logger.info(
                        "  old: %s  new: %s",
                        result.old_addresses.format(),
                        result.new_addresses.format(),
                    )
        else:
            wx.CallAfter(self._set_profile_status, name, STATUS_FAIL)
            with ProfileLogContext(name):
                self.logger.error("Profile %s failed: %s", name, result.message)
                for diag in result.diagnose_results:
                    self.logger.error("  [%s] %s: %s", diag.type_name, diag.summary, ", ".join(diag.addresses))

    def apply_proxy(self, proxy: str | None, save_to_config: bool = False) -> None:
        self.proxy = proxy
        restore_proxy_env(self._proxy_backup)
        self._proxy_backup = apply_proxy_env(proxy)
        if save_to_config and self.config_path:
            save_config(self.config_path, {"proxy": proxy or ""})
            self.logger.info("Saved proxy to %s", self.config_path)
        # Proxy change can alter egress IP; drop cached client IP and rediscover.
        self.cli_options.pop("client_ip", None)
        self.cli_options.pop("client_ip_expire", None)
        self._public_ip = None
        self._public_ip_loading = True
        self._update_status_bar()
        self._start_public_ip_fetch()
        self._set_action("Proxy updated")
