"""Aliyun Elastic IP handler."""

from __future__ import annotations

import hashlib
import hmac
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
        response.raise_for_status()
        payload = response.json()
        if "Code" in payload:
            raise RuntimeError(f"{payload.get('Code')}: {payload.get('Message', 'Aliyun API error')}")
        return payload

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

    def reallocate(self) -> ReallocateResult:
        region = self._ensure_region()
        if not region:
            return ReallocateResult(False, message="region not configured")

        instance = self._find_primary_instance(region=region)
        if not instance:
            return ReallocateResult(False, message="no running ECS instance with public IPv4 found")

        old_ip = instance["public_ip"]
        instance_id = instance["instance_id"]

        self.report_progress(0.15, f"Looking up Elastic IP allocation for {old_ip}")
        eip_payload = self._request("DescribeEipAddresses", {"EipAddress": old_ip}, region=region)
        eips = eip_payload.get("EipAddresses", {}).get("EipAddress", [])
        if not eips:
            return ReallocateResult(False, old_ip=old_ip, message=f"Elastic IP {old_ip} not found")

        allocation_id = eips[0].get("AllocationId")

        self.report_progress(0.35, f"Unassociating Elastic IP {old_ip}")
        self._request(
            "UnassociateEipAddress",
            {"AllocationId": allocation_id, "InstanceId": instance_id},
            region=region,
        )

        self.report_progress(0.55, f"Releasing Elastic IP {old_ip}")
        self._request("ReleaseEipAddress", {"AllocationId": allocation_id}, region=region)

        self.report_progress(0.75, f"Allocating new Elastic IP in {region}")
        alloc = self._request("AllocateEipAddress", {"Bandwidth": "5"}, region=region)
        new_ip = alloc.get("EipAddress")
        new_alloc = alloc.get("AllocationId")
        if not is_ipv4(new_ip):
            return ReallocateResult(False, old_ip=old_ip, message="AllocateEipAddress returned invalid IP")

        self.report_progress(0.9, f"Associating {new_ip} with {instance_id}")
        self._request(
            "AssociateEipAddress",
            {"AllocationId": new_alloc, "InstanceId": instance_id},
            region=region,
        )

        self.report_progress(1.0, f"Reallocated {old_ip} -> {new_ip}")
        return ReallocateResult(True, old_ip=old_ip, new_ip=new_ip, message=f"{old_ip} -> {new_ip}")
