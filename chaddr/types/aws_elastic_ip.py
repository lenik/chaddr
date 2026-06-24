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

    def _find_primary_instance(self, ec2):
        region = self._resolve_region()
        instance_id = self.config.get("instance") or self.config.get("instance_id")
        if instance_id:
            response = ec2.describe_instances(InstanceIds=[instance_id])
            reservations = response.get("Reservations", [])
            if reservations:
                return self._extract_primary(reservations[0]["Instances"][0])
            return None

        self.logger.info("Searching running instances in region %s", region)
        response = ec2.describe_instances(
            Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
        )
        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                found = self._extract_primary(instance)
                if found:
                    return found
        return None

    def _extract_primary(self, instance) -> dict | None:
        for iface in instance.get("NetworkInterfaces", []):
            for private in iface.get("PrivateIpAddresses", []):
                if private.get("Primary"):
                    pub = private.get("Association", {}).get("PublicIp")
                    if is_ipv4(pub):
                        return {
                            "instance_id": instance["InstanceId"],
                            "private_ip": private.get("PrivateIpAddress"),
                            "public_ip": pub,
                        }
        return None

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

        try:
            instance = self._find_primary_instance(ec2)
            if instance:
                addresses.append(instance["public_ip"])
                items.append(
                    DiagnoseItem(
                        "instance",
                        True,
                        f'{instance["instance_id"]} primary public IP {instance["public_ip"]}',
                    )
                )
            else:
                items.append(
                    DiagnoseItem(
                        "instance",
                        False,
                        "no running instance with primary public IPv4 found",
                        "Start an EC2 instance in this region or attach an Elastic IP.",
                    )
                )
        except Exception as exc:
            items.append(
                DiagnoseItem(
                    "instance lookup",
                    False,
                    str(exc),
                    "Verify EC2 permissions: DescribeInstances, DescribeAddresses.",
                )
            )

        ok = all(item.ok for item in items)
        return DiagnoseResult(self.type_name, "ready" if ok else "issues found", ok, items, addresses)

    def reallocate(self) -> ReallocateResult:
        region = self._resolve_region()
        if not region:
            return ReallocateResult(False, message="region not configured")

        ec2 = self._client()
        instance = self._find_primary_instance(ec2)
        if not instance:
            return ReallocateResult(False, message="no running instance with primary public IPv4 found")

        instance_id = instance["instance_id"]
        private_ip = instance["private_ip"]
        old_ip = instance["public_ip"]

        self.report_progress(0.1, f"Disassociating Elastic IP {old_ip} from {instance_id}")
        try:
            ec2.disassociate_address(PublicIp=old_ip)
        except Exception as exc:
            self.logger.warning("Disassociate failed or already disassociated: %s", exc)

        self.report_progress(0.25, f"Looking up allocation id for {old_ip}")
        alloc_id = None
        addresses = ec2.describe_addresses(PublicIps=[old_ip]).get("Addresses", [])
        if addresses:
            alloc_id = addresses[0].get("AllocationId")

        if alloc_id:
            self.report_progress(0.4, f"Releasing Elastic IP {old_ip} ({alloc_id})")
            ec2.release_address(AllocationId=alloc_id)

        self.report_progress(0.6, f"Allocating new Elastic IP in {region}")
        alloc = ec2.allocate_address(Domain="vpc")
        new_ip = alloc.get("PublicIp")
        if not is_ipv4(new_ip):
            return ReallocateResult(False, old_ip=old_ip, message="allocate-address returned invalid IP")

        self.report_progress(0.8, f"Associating {new_ip} with {instance_id} ({private_ip})")
        ec2.associate_address(
            InstanceId=instance_id,
            PublicIp=new_ip,
            AllowReassociation=True,
            PrivateIpAddress=private_ip,
        )

        self.report_progress(1.0, f"Reallocated {old_ip} -> {new_ip}")
        return ReallocateResult(True, old_ip=old_ip, new_ip=new_ip, message=f"{old_ip} -> {new_ip}")
