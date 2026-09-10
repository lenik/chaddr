"""AWS Elastic IP handler (disassociate, release, allocate, associate)."""

from __future__ import annotations

from chaddr.proxy import boto_config
from chaddr.types.base import (
    AddressTypeHandler,
    DiagnoseItem,
    DiagnoseResult,
    ReallocateResult,
    is_ipv4,
)

REGION_ALIASES = {
    "sg1": "ap-southeast-1",
    "kr1": "ap-northeast-2",
    "hk1": "ap-east-1",
    "jp1": "ap-northeast-1",
    "jp2": "ap-northeast-3",
    "au1": "ap-southeast-2",
    "us1": "us-west-1",
    "us2": "us-east-1",
}


class AwsElasticIpHandler(AddressTypeHandler):
    type_name = "aws elastic ip"
    supports_manual_edit = False
    supports_reallocate = True

    def _resolve_region(self) -> str:
        region = self.config.get("region", "").strip()
        return REGION_ALIASES.get(region, region)

    def _client(self):
        import boto3

        kwargs: dict = {}
        access_key = self.options.get("aws_access_key_id")
        secret_key = self.options.get("aws_secret_access_key") or self.options.get("aws_ecret_access_key")
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key
        config = boto_config(self.proxy)
        if config:
            kwargs["config"] = config
        return boto3.client("ec2", region_name=self._resolve_region(), **kwargs)

    def _nic_id(self) -> str:
        return (
            self.config.get("nic")
            or self.config.get("eni")
            or self.config.get("network_interface")
            or self.config.get("network_interface_id")
            or ""
        ).strip()

    def _instance_id(self) -> str:
        return (self.config.get("instance") or self.config.get("instance_id") or "").strip()

    def _scope_label(self) -> str:
        """GUI / diagnose label: ec2 nic | ec2."""
        from chaddr.profile import canonical_from_type_label

        explicit = (self.config.get("from_type") or "").strip()
        if explicit:
            return canonical_from_type_label(explicit)
        if self._nic_id():
            return "ec2 nic"
        return "ec2"

    def _is_nic_scope(self) -> bool:
        label = self._scope_label()
        return label.endswith(" nic") or bool(self._nic_id())

    @staticmethod
    def _bindings_from_iface(iface: dict, instance_id: str | None = None) -> list[dict]:
        """Public IPv4 bindings on one ENI (primary private preferred first)."""
        eni_id = iface.get("NetworkInterfaceId") or ""
        attachment = iface.get("Attachment") or {}
        attached_instance = instance_id or attachment.get("InstanceId") or ""
        primary: list[dict] = []
        secondary: list[dict] = []
        for private in iface.get("PrivateIpAddresses", []):
            pub = (private.get("Association") or {}).get("PublicIp")
            if not is_ipv4(pub):
                continue
            binding = {
                "instance_id": attached_instance,
                "network_interface_id": eni_id,
                "private_ip": private.get("PrivateIpAddress"),
                "public_ip": pub,
                "primary_private": bool(private.get("Primary")),
            }
            if private.get("Primary"):
                primary.append(binding)
            else:
                secondary.append(binding)
        return primary + secondary

    def _bindings_from_instance(self, instance: dict) -> list[dict]:
        """All public IPv4 bindings on every NIC of the instance."""
        instance_id = instance["InstanceId"]
        bindings: list[dict] = []
        for iface in instance.get("NetworkInterfaces", []):
            bindings.extend(self._bindings_from_iface(iface, instance_id))
        if bindings:
            return bindings
        # Fallback when NetworkInterfaces omit associations.
        pub = instance.get("PublicIpAddress")
        private = instance.get("PrivateIpAddress")
        if is_ipv4(pub):
            return [
                {
                    "instance_id": instance_id,
                    "network_interface_id": "",
                    "private_ip": private,
                    "public_ip": pub,
                    "primary_private": True,
                }
            ]
        return []

    def _find_bindings(self, ec2) -> list[dict]:
        """Resolve public IP bindings from nic: or instance: scope."""
        nic_id = self._nic_id()
        if nic_id:
            response = ec2.describe_network_interfaces(NetworkInterfaceIds=[nic_id])
            interfaces = response.get("NetworkInterfaces", [])
            if not interfaces:
                return []
            return self._bindings_from_iface(interfaces[0])

        instance_id = self._instance_id()
        if instance_id:
            response = ec2.describe_instances(InstanceIds=[instance_id])
            reservations = response.get("Reservations", [])
            if not reservations:
                return []
            return self._bindings_from_instance(reservations[0]["Instances"][0])

        region = self._resolve_region()
        self.logger.info("Searching running instances in region %s", region)
        response = ec2.describe_instances(
            Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
        )
        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                found = self._bindings_from_instance(instance)
                if found:
                    return found
        return []

    def diagnose(self) -> DiagnoseResult:
        items: list[DiagnoseItem] = []
        addresses: list[str] = []
        region = self._resolve_region()

        if not region:
            items.append(
                DiagnoseItem(
                    "region",
                    False,
                    "missing region in profile",
                    'Add a line like "region: ap-northeast-2" to the profile entry.',
                )
            )
            return DiagnoseResult(self.type_name, "region not configured", False, items, addresses)

        items.append(DiagnoseItem("region", True, region))

        try:
            ec2 = self._client()
            ec2.describe_regions(RegionNames=[region])
            items.append(DiagnoseItem("aws api", True, f"connected to {region}"))
        except Exception as exc:
            items.append(
                DiagnoseItem(
                    "aws api",
                    False,
                    str(exc),
                    "Check AWS credentials, region name, and proxy settings.",
                )
            )
            return DiagnoseResult(self.type_name, "AWS API unavailable", False, items, addresses)

        nic_id = self._nic_id()
        instance_id = self._instance_id()
        scope_label = self._scope_label()
        try:
            bindings = self._find_bindings(ec2)
            if bindings:
                for binding in bindings:
                    addresses.append(binding["public_ip"])
                summary_ips = ", ".join(binding["public_ip"] for binding in bindings)
                if self._is_nic_scope() and nic_id:
                    detail = f"{nic_id} public IP {summary_ips}"
                    if bindings[0].get("instance_id"):
                        detail = f"{bindings[0]['instance_id']} {detail}"
                else:
                    detail = f"{bindings[0]['instance_id']} public IP {summary_ips}"
                items.append(DiagnoseItem(scope_label, True, detail))
            else:
                if nic_id:
                    guidance = "Attach an Elastic IP to a private address on this ENI."
                    detail = f"no public IPv4 on NIC {nic_id}"
                elif instance_id:
                    guidance = "Attach an Elastic IP to the instance or one of its ENIs."
                    detail = f"no public IPv4 on instance {instance_id}"
                else:
                    guidance = "Start an EC2 instance in this region or attach an Elastic IP."
                    detail = "no running instance with public IPv4 found"
                items.append(DiagnoseItem(scope_label, False, detail, guidance))
        except Exception as exc:
            items.append(
                DiagnoseItem(
                    f"{scope_label} lookup",
                    False,
                    str(exc),
                    "Verify EC2 permissions: DescribeInstances, DescribeNetworkInterfaces, DescribeAddresses.",
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

    def _reallocate_release_candidates(self, binding_public_ips: list[str]) -> set[str]:
        # Only release IPs we are about to replace — do not touch other NICs via spare.
        return {ip for ip in binding_public_ips if is_ipv4(ip)}

    def _bindings_for_reallocate(self, bindings: list[dict]) -> list[dict]:
        """NIC scope: all bindings on that ENI. Instance scope: only history/spare matches."""
        if self._is_nic_scope():
            return bindings
        spare = self._effective_spare()
        history_ips = {ip for ip in spare.ipv4 if is_ipv4(ip)}
        if not history_ips:
            self.logger.warning(
                "ec2 instance renew: no addr-history/spare IPs; refusing to touch all public IPs"
            )
            return []
        matched = [binding for binding in bindings if binding.get("public_ip") in history_ips]
        skipped = [
            binding["public_ip"]
            for binding in bindings
            if binding.get("public_ip") not in history_ips
        ]
        if skipped:
            self.logger.info(
                "ec2 instance renew: skipping public IP(s) not in history: %s",
                ", ".join(skipped),
            )
        if matched:
            self.logger.info(
                "ec2 instance renew: history-matched public IP(s): %s",
                ", ".join(binding["public_ip"] for binding in matched),
            )
        return matched

    def _release_owned_elastic_ips(self, ec2, candidates: set[str]) -> int:
        self.report_progress(0.08, "Listing Elastic IPs in region")
        all_addresses = ec2.describe_addresses().get("Addresses", [])
        matching = [item for item in all_addresses if item.get("PublicIp") in candidates]
        if not matching:
            self.logger.info(
                "No owned Elastic IPs match %d candidate address(es): %s",
                len(candidates),
                ", ".join(sorted(candidates)) or "(none)",
            )
            return 0

        released = 0
        total = len(matching)
        for index, addr in enumerate(matching):
            public_ip = addr.get("PublicIp", "")
            alloc_id = addr.get("AllocationId")
            association_id = addr.get("AssociationId")
            fraction = 0.1 + 0.4 * (index / max(total, 1))

            if association_id:
                self.report_progress(fraction, f"Disassociating Elastic IP {public_ip}")
                try:
                    ec2.disassociate_address(AssociationId=association_id)
                except Exception as exc:
                    self.logger.warning("Disassociate %s failed: %s", public_ip, exc)

            if alloc_id:
                self.report_progress(
                    min(fraction + 0.05, 0.49),
                    f"Releasing Elastic IP {public_ip} ({alloc_id})",
                )
                try:
                    ec2.release_address(AllocationId=alloc_id)
                    released += 1
                    self.logger.info("Released Elastic IP %s (%s)", public_ip, alloc_id)
                except Exception as exc:
                    self.logger.warning("Release %s failed: %s", public_ip, exc)
        return released

    def _associate_binding(self, ec2, *, allocation_id: str, binding: dict) -> None:
        """Associate a new EIP onto the same private-IP slot (primary or secondary)."""
        private_ip = binding.get("private_ip")
        eni_id = binding.get("network_interface_id") or ""
        instance_id = binding.get("instance_id") or ""
        slot = "primary private" if binding.get("primary_private") else "secondary private"
        if eni_id and private_ip:
            self.logger.info(
                "Associating allocation %s with NIC %s %s address %s",
                allocation_id,
                eni_id,
                slot,
                private_ip,
            )
            ec2.associate_address(
                AllocationId=allocation_id,
                NetworkInterfaceId=eni_id,
                PrivateIpAddress=private_ip,
                AllowReassociation=True,
            )
            return
        if instance_id and private_ip:
            self.logger.info(
                "Associating allocation %s with instance %s private address %s",
                allocation_id,
                instance_id,
                private_ip,
            )
            ec2.associate_address(
                AllocationId=allocation_id,
                InstanceId=instance_id,
                PrivateIpAddress=private_ip,
                AllowReassociation=True,
            )
            return
        raise RuntimeError("binding missing network interface / instance and private IP for association")

    def reallocate(self) -> ReallocateResult:
        region = self._resolve_region()
        if not region:
            return ReallocateResult(False, message="region not configured")

        ec2 = self._client()
        bindings = self._find_bindings(ec2)
        if not bindings:
            nic_id = self._nic_id()
            if nic_id:
                return ReallocateResult(False, message=f"no public IPv4 on NIC {nic_id}")
            instance_id = self._instance_id()
            if instance_id:
                return ReallocateResult(False, message=f"no public IPv4 on instance {instance_id}")
            return ReallocateResult(False, message="no running instance with public IPv4 found")

        bindings = self._bindings_for_reallocate(bindings)
        if not bindings:
            return ReallocateResult(
                False,
                message=(
                    "no public IPs match addr-history/spare; "
                    "add the IP to addr-history or use from: ec2 nic"
                ),
            )

        old_ips = [binding["public_ip"] for binding in bindings]
        candidates = self._reallocate_release_candidates(old_ips)
        self.logger.info(
            "Reallocate release candidates: %s",
            ", ".join(sorted(candidates)) or "(none)",
        )
        released = self._release_owned_elastic_ips(ec2, candidates)
        if released:
            self.report_progress(0.5, f"Released {released} Elastic IP(s)")
        else:
            self.report_progress(
                0.2,
                "No owned Elastic IPs to release among current address candidates; allocating new address",
            )

        new_ips: list[str] = []
        total = len(bindings)
        for index, binding in enumerate(bindings):
            fraction = 0.55 + 0.4 * (index / max(total, 1))
            self.report_progress(fraction, f"Allocating new Elastic IP in {region}")
            alloc = ec2.allocate_address(Domain="vpc")
            new_ip = alloc.get("PublicIp")
            allocation_id = alloc.get("AllocationId")
            if not is_ipv4(new_ip) or not allocation_id:
                return ReallocateResult(
                    False,
                    old_ip=old_ips[0],
                    message="allocate-address returned invalid IP/allocation",
                )
            slot = "primary private" if binding.get("primary_private") else "secondary private"
            target = binding.get("network_interface_id") or binding.get("instance_id")
            self.report_progress(
                min(fraction + 0.05, 0.95),
                f"Associating {new_ip} with {target} ({slot} {binding.get('private_ip')})",
            )
            self._associate_binding(ec2, allocation_id=allocation_id, binding=binding)
            new_ips.append(new_ip)

        old_ip = old_ips[0]
        new_ip = new_ips[0]
        message = ", ".join(f"{old} -> {new}" for old, new in zip(old_ips, new_ips))
        self.report_progress(1.0, f"Reallocated {message}")
        return ReallocateResult(True, old_ip=old_ip, new_ip=new_ip, message=message)
