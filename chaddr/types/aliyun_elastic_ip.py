"""Aliyun Elastic IP handler."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

from chaddr.proxy import requests_proxies
from chaddr.types.base import (
    AddressTypeHandler,
    DiagnoseItem,
    DiagnoseResult,
    ReallocateResult,
    is_ipv4,
)

REGION_ALIASES = {
    "cn1": "cn-hangzhou",
    "cn2": "cn-shanghai",
    "cn3": "cn-beijing",
    "sg1": "ap-southeast-1",
    "hk1": "cn-hongkong",
}

DISCOVERY_REGIONS = sorted(
    {
        *REGION_ALIASES.values(),
        "cn-hangzhou",
        "cn-shanghai",
        "cn-beijing",
        "cn-shenzhen",
        "cn-qingdao",
        "cn-zhangjiakou",
        "cn-huhehaote",
        "cn-wulanchabu",
        "ap-southeast-1",
        "ap-southeast-3",
        "ap-southeast-5",
        "ap-northeast-1",
        "cn-hongkong",
        "us-west-1",
        "eu-central-1",
    }
)


class AliyunElasticIpHandler(AddressTypeHandler):
    type_name = "aliyun elastic ip"
    supports_manual_edit = False
    supports_reallocate = True

    API_VERSION = "2014-05-26"

    def _resolve_region(self) -> str:
        region = self.config.get("region", "").strip()
        return REGION_ALIASES.get(region, region)

    def _credentials(self) -> tuple[str, str]:
        access_key = self.options.get("aliyun_access_key_id") or self.options.get("access_key_id")
        secret_key = self.options.get("aliyun_access_key_secret") or self.options.get("access_key_secret")
        if not access_key or not secret_key:
            raise RuntimeError("Aliyun credentials not configured (access key / secret key)")
        return access_key, secret_key

    def _sign(self, params: dict[str, str], secret_key: str) -> str:
        import base64

        sorted_params = sorted(params.items())
        canonical = "&".join(f"{quote(k, safe='~')}={quote(v, safe='~')}" for k, v in sorted_params)
        string_to_sign = f"GET&%2F&{quote(canonical, safe='~')}"
        digest = hmac.new((secret_key + "&").encode(), string_to_sign.encode(), hashlib.sha1).digest()
        return base64.b64encode(digest).decode()

    def _request(self, action: str, extra: dict[str, str] | None = None, *, region: str | None = None) -> dict:
        region = region or self._resolve_region()
        if not region:
            raise RuntimeError("region not configured")
        access_key, secret_key = self._credentials()
        params = {
            "Format": "JSON",
            "Version": self.API_VERSION,
            "AccessKeyId": access_key,
            "SignatureMethod": "HMAC-SHA1",
            "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "SignatureVersion": "1.0",
            "SignatureNonce": str(uuid.uuid4()),
            "Action": action,
            "RegionId": region,
        }
        if extra:
            params.update(extra)
        params["Signature"] = self._sign(params, secret_key)
        url = f"https://ecs.{region}.aliyuncs.com"
        response = requests.get(url, params=params, timeout=60, proxies=requests_proxies(self.proxy))
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("Code"):
            raise RuntimeError(f"{payload.get('Code')}: {payload.get('Message', 'Aliyun API error')}")
        response.raise_for_status()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Aliyun API returned non-JSON response for {action}")
        return payload

    def _get_instance_detail(self, instance_id: str, *, region: str) -> dict | None:
        payload = self._request(
            "DescribeInstances",
            {"InstanceIds": json.dumps([instance_id])},
            region=region,
        )
        instances = payload.get("Instances", {}).get("Instance", [])
        if isinstance(instances, dict):
            return instances if instances.get("InstanceId") == instance_id else None
        for instance in instances:
            if instance.get("InstanceId") == instance_id:
                return instance
        return None

    @staticmethod
    def _instance_public_ipv4(instance: dict) -> str | None:
        eip = instance.get("EipAddress") or {}
        eip_ip = eip.get("IpAddress")
        if is_ipv4(eip_ip):
            return eip_ip
        public_ips = instance.get("PublicIpAddress", {}).get("IpAddress", []) or []
        if isinstance(public_ips, str):
            public_ips = [public_ips]
        for ip in public_ips:
            if is_ipv4(ip):
                return ip
        return None

    def _collect_instance_address_candidates(self, instance_id: str, *, region: str) -> set[str]:
        candidates: set[str] = set()
        instance = self._get_instance_detail(instance_id, region=region)
        if instance is None:
            return candidates
        ip = self._instance_public_ipv4(instance)
        if ip:
            candidates.add(ip)
        eip = instance.get("EipAddress") or {}
        eip_ip = eip.get("IpAddress")
        if is_ipv4(eip_ip):
            candidates.add(eip_ip)
        public_ips = instance.get("PublicIpAddress", {}).get("IpAddress", []) or []
        if isinstance(public_ips, str):
            public_ips = [public_ips]
        for item in public_ips:
            if is_ipv4(item):
                candidates.add(item)
        return candidates

    def _prepare_instance_public_ip(self, instance_id: str, *, region: str) -> None:
        """Convert VPC NAT public IP to EIP when needed so it can be released/rebound."""
        instance = self._get_instance_detail(instance_id, region=region)
        if instance is None:
            return
        eip = instance.get("EipAddress") or {}
        if eip.get("AllocationId") and eip.get("IpAddress"):
            return
        public_ips = instance.get("PublicIpAddress", {}).get("IpAddress", []) or []
        if isinstance(public_ips, str):
            public_ips = [public_ips]
        if not public_ips:
            return
        public_ip = public_ips[0]
        self.report_progress(0.05, f"Converting NAT public IP {public_ip} to Elastic IP")
        self._request("ConvertNatPublicIpToEip", {"InstanceId": instance_id}, region=region)
        self.logger.info("Converted NAT public IP %s on %s to Elastic IP", public_ip, instance_id)

    def _find_primary_instance(self, *, region: str | None = None) -> dict | None:
        instance_id = self.config.get("instance") or self.config.get("instance_id")
        payload = self._request("DescribeInstances", {"Status": "Running", "PageSize": "50"}, region=region)
        instances = payload.get("Instances", {}).get("Instance", [])
        if instance_id:
            for instance in instances:
                if instance.get("InstanceId") == instance_id:
                    instances = [instance]
                    break
        for instance in instances:
            public_ips = instance.get("PublicIpAddress", {}).get("IpAddress", [])
            eip = instance.get("EipAddress", {}) or {}
            pub = eip.get("IpAddress") or (public_ips[0] if public_ips else None)
            private_ips = instance.get("VpcAttributes", {}).get("PrivateIpAddress", {}).get("IpAddress", [])
            if pub and private_ips and is_ipv4(pub):
                return {
                    "instance_id": instance["InstanceId"],
                    "public_ip": pub,
                    "region": region or self._resolve_region(),
                }
        return None

    def _discover_region(self) -> str | None:
        try:
            self._credentials()
        except Exception:
            return None

        for region in DISCOVERY_REGIONS:
            try:
                if self._find_primary_instance(region=region):
                    self.logger.info("Discovered Aliyun ECS region: %s", region)
                    return region
            except Exception as exc:
                self.logger.debug("Aliyun region probe %s: %s", region, exc)
        return None

    def _persist_region(self, region: str) -> None:
        from chaddr.profile import update_profile_entry_option

        self.config["region"] = region
        profile_path = self._profile_path
        if profile_path is None:
            return
        if update_profile_entry_option(Path(profile_path), self.type_name, "region", region):
            self.logger.info("Saved region %s to profile %s", region, profile_path)
        else:
            self.logger.warning("Could not write region to profile %s", profile_path)

    def _ensure_region(self) -> str:
        region = self._resolve_region()
        if region:
            return region
        discovered = self._discover_region()
        if not discovered:
            return ""
        self._persist_region(discovered)
        return discovered

    def diagnose(self) -> DiagnoseResult:
        items: list[DiagnoseItem] = []
        addresses: list[str] = []

        try:
            self._credentials()
            items.append(DiagnoseItem("credentials", True, "access key configured"))
        except Exception as exc:
            items.append(
                DiagnoseItem(
                    "credentials",
                    False,
                    str(exc),
                    "Provide --aliyun-access-key-id and --aliyun-access-key-secret.",
                )
            )
            return DiagnoseResult(self.type_name, "credentials missing", False, items, addresses)

        configured_region = self._resolve_region()
        if not configured_region:
            self.report_progress(0.05, "Searching Aliyun regions for ECS instance...")
            region = self._ensure_region()
            if not region:
                items.append(
                    DiagnoseItem(
                        "region",
                        False,
                        "missing region in profile",
                        'Add "region: cn-hangzhou" or ensure credentials can list ECS instances.',
                    )
                )
                return DiagnoseResult(self.type_name, "region not configured", False, items, addresses)
            items.append(DiagnoseItem("region", True, f"{region} (auto-discovered)"))
        else:
            region = configured_region
            items.append(DiagnoseItem("region", True, region))

        try:
            instance = self._find_primary_instance(region=region)
            if instance:
                addresses.append(instance["public_ip"])
                items.append(
                    DiagnoseItem(
                        "instance",
                        True,
                        f'{instance["instance_id"]} public IP {instance["public_ip"]}',
                    )
                )
            else:
                items.append(
                    DiagnoseItem(
                        "instance",
                        False,
                        "no running ECS instance with public IPv4 found",
                        "Start an ECS instance or bind an Elastic IP in this region.",
                    )
                )
        except Exception as exc:
            items.append(
                DiagnoseItem(
                    "aliyun api",
                    False,
                    str(exc),
                    "Check Aliyun credentials, region, and proxy settings.",
                )
            )

        ok = all(item.ok for item in items)
        return DiagnoseResult(self.type_name, "ready" if ok else "issues found", ok, items, addresses)

    def _effective_spare(self):
        from chaddr.address import SpareFromAddresses

        if self._spare_from_addresses and not self._spare_from_addresses.is_empty():
            return self._spare_from_addresses
        if self._source_addresses and not self._source_addresses.is_empty():
            return SpareFromAddresses.from_address_sets(self._source_addresses)
        return SpareFromAddresses()

    def _reallocate_release_candidates(self, instance_public_ip: str) -> set[str]:
        candidates: set[str] = set()
        if is_ipv4(instance_public_ip):
            candidates.add(instance_public_ip)
        for ip in self._effective_spare().ipv4:
            if is_ipv4(ip):
                candidates.add(ip)
        return candidates

    @staticmethod
    def _eip_records(payload: dict) -> list[dict]:
        eips = payload.get("EipAddresses", {}).get("EipAddress", [])
        if not eips:
            return []
        if isinstance(eips, dict):
            return [eips]
        return list(eips)

    def _list_elastic_ips(self, *, region: str) -> list[dict]:
        payload = self._request(
            "DescribeEipAddresses",
            {"PageSize": "100", "PageNumber": "1"},
            region=region,
        )
        return self._eip_records(payload)

    def _wait_eip_available(self, allocation_id: str, *, region: str, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for eip in self._list_elastic_ips(region=region):
                if eip.get("AllocationId") != allocation_id:
                    continue
                if (eip.get("Status") or "").lower() == "available":
                    return True
                break
            time.sleep(1)
        return False

    def _release_owned_elastic_ips(self, candidates: set[str], *, region: str) -> int:
        self.report_progress(0.08, "Listing Elastic IPs in region")
        all_eips = self._list_elastic_ips(region=region)
        matching = [
            item
            for item in all_eips
            if item.get("IpAddress") in candidates or item.get("EipAddress") in candidates
        ]
        if not matching:
            self.logger.info(
                "No owned Elastic IPs match %d candidate address(es): %s",
                len(candidates),
                ", ".join(sorted(candidates)) or "(none)",
            )
            return 0

        released = 0
        total = len(matching)
        for index, eip in enumerate(matching):
            public_ip = eip.get("IpAddress") or eip.get("EipAddress") or ""
            allocation_id = eip.get("AllocationId")
            if not allocation_id:
                continue
            fraction = 0.1 + 0.4 * (index / max(total, 1))

            status = (eip.get("Status") or "").lower()
            bound_instance = eip.get("InstanceId") or ""
            if status == "inuse" or bound_instance:
                self.report_progress(fraction, f"Unassociating Elastic IP {public_ip}")
                try:
                    params: dict[str, str] = {"AllocationId": allocation_id}
                    if bound_instance:
                        params["InstanceId"] = bound_instance
                    self._request(
                        "UnassociateEipAddress",
                        params,
                        region=region,
                    )
                except Exception as exc:
                    self.logger.warning("Unassociate %s failed: %s", public_ip, exc)
                    continue
                if not self._wait_eip_available(allocation_id, region=region):
                    self.logger.warning(
                        "Elastic IP %s did not become Available after unassociate; skipping release",
                        public_ip,
                    )
                    continue

            self.report_progress(
                min(fraction + 0.05, 0.49),
                f"Releasing Elastic IP {public_ip} ({allocation_id})",
            )
            try:
                self._request(
                    "ReleaseEipAddress",
                    {"AllocationId": allocation_id},
                    region=region,
                )
                released += 1
                self.logger.info("Released Elastic IP %s (%s)", public_ip, allocation_id)
            except Exception as exc:
                self.logger.warning("Release %s failed: %s", public_ip, exc)
        return released

    def reallocate(self) -> ReallocateResult:
        region = self._ensure_region()
        if not region:
            return ReallocateResult(False, message="region not configured")

        instance = self._find_primary_instance(region=region)
        if not instance:
            return ReallocateResult(False, message="no running ECS instance with public IPv4 found")

        old_ip = instance["public_ip"]
        instance_id = instance["instance_id"]

        self._prepare_instance_public_ip(instance_id, region=region)

        candidates = self._reallocate_release_candidates(old_ip)
        candidates.update(self._collect_instance_address_candidates(instance_id, region=region))
        self.logger.info(
            "Reallocate release candidates: %s",
            ", ".join(sorted(candidates)) or "(none)",
        )
        released = self._release_owned_elastic_ips(candidates, region=region)
        if released:
            self.report_progress(0.5, f"Released {released} Elastic IP(s)")
        else:
            self.report_progress(
                0.2,
                "No owned Elastic IPs to release among current address candidates; allocating new address",
            )

        self.report_progress(0.6, f"Allocating new Elastic IP in {region}")
        alloc = self._request("AllocateEipAddress", {"Bandwidth": "5"}, region=region)
        new_ip = alloc.get("EipAddress")
        new_alloc = alloc.get("AllocationId")
        if not is_ipv4(new_ip):
            return ReallocateResult(False, old_ip=old_ip, message="AllocateEipAddress returned invalid IP")

        self.report_progress(0.85, f"Associating {new_ip} with {instance_id}")
        self._request(
            "AssociateEipAddress",
            {"AllocationId": new_alloc, "InstanceId": instance_id},
            region=region,
        )

        self.report_progress(1.0, f"Reallocated {old_ip} -> {new_ip}")
        return ReallocateResult(True, old_ip=old_ip, new_ip=new_ip, message=f"{old_ip} -> {new_ip}")
