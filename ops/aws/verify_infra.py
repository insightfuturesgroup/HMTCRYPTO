"""Read-only Mumbai EC2 verification with fixed, identifier-free output."""

from __future__ import annotations

import getpass
import json
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Any

REGION = "ap-south-1"


class CheckFailure(Exception):
    """Do not include AWS output or identifiers in diagnostics."""


def aws(service: str, operation: str, *arguments: str) -> dict:
    try:
        result = subprocess.run(
            [
                "aws",
                "--region",
                REGION,
                "--no-cli-pager",
                service,
                operation,
                *arguments,
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )
        if result.returncode:
            raise CheckFailure
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise CheckFailure
        return value
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise CheckFailure from None


def verify(instance_id: str, call: Callable[..., Any] = aws) -> list[tuple[str, bool]]:
    if not re.fullmatch(r"i-[0-9a-f]{17}", instance_id):
        raise CheckFailure
    response = call("ec2", "describe-instances", "--instance-ids", instance_id)
    instances = [i for r in response.get("Reservations", []) for i in r.get("Instances", [])]
    if len(instances) != 1:
        raise CheckFailure
    instance = instances[0]
    metadata = instance.get("MetadataOptions", {})
    checks = [
        ("instance running", instance.get("State", {}).get("Name") == "running"),
        (
            "t4g.medium arm64",
            instance.get("InstanceType") == "t4g.medium"
            and instance.get("Architecture") == "arm64",
        ),
        ("IMDSv2 required", metadata.get("HttpTokens") == "required"),
        ("IMDS hop limit 2", metadata.get("HttpPutResponseHopLimit") == 2),
    ]
    groups = instance.get("SecurityGroups", [])
    permissions = []
    for group in groups:
        described = call("ec2", "describe-security-groups", "--group-ids", group["GroupId"])
        for item in described.get("SecurityGroups", []):
            permissions.extend(item.get("IpPermissions", []))
    allowed = (
        bool(groups)
        and bool(permissions)
        and all(
            p.get("IpProtocol") == "tcp"
            and p.get("FromPort") == p.get("ToPort")
            and p.get("FromPort") in (80, 443)
            for p in permissions
        )
    )
    ports = {p.get("FromPort") for p in permissions}
    checks.append(("inbound TCP 80/443 only; no SSH", allowed and {80, 443} <= ports))
    termination = call(
        "ec2",
        "describe-instance-attribute",
        "--instance-id",
        instance_id,
        "--attribute",
        "disableApiTermination",
    )
    checks.append(
        (
            "termination protection",
            termination.get("DisableApiTermination", {}).get("Value") is True,
        )
    )
    volume_ids = [
        v["Ebs"]["VolumeId"] for v in instance.get("BlockDeviceMappings", []) if "Ebs" in v
    ]
    volumes = (
        call("ec2", "describe-volumes", "--volume-ids", *volume_ids).get("Volumes", [])
        if volume_ids
        else []
    )
    checks.append(
        (
            "all EBS encrypted gp3; total at least 30 GiB",
            len(volumes) == len(volume_ids)
            and bool(volumes)
            and all(v.get("Encrypted") is True and v.get("VolumeType") == "gp3" for v in volumes)
            and sum(v.get("Size", 0) for v in volumes) >= 30,
        )
    )
    addresses = call(
        "ec2", "describe-addresses", "--filters", f"Name=instance-id,Values={instance_id}"
    ).get("Addresses", [])
    checks.append(
        (
            "Elastic IP associated",
            bool(addresses)
            and any(
                a.get("InstanceId") == instance_id and a.get("AssociationId") for a in addresses
            ),
        )
    )
    ssm = call(
        "ssm", "describe-instance-information", "--filters", f"Key=InstanceIds,Values={instance_id}"
    ).get("InstanceInformationList", [])
    checks.append(
        (
            "SSM online",
            any(
                i.get("InstanceId") == instance_id and i.get("PingStatus") == "Online" for i in ssm
            ),
        )
    )
    summaries = call("dlm", "get-lifecycle-policies").get("Policies", [])
    covered = set()
    for summary in summaries:
        if summary.get("State") != "ENABLED":
            continue
        policy = call("dlm", "get-lifecycle-policy", "--policy-id", summary["PolicyId"])["Policy"]
        details = policy.get("PolicyDetails", {})
        targets = details.get("TargetTags", [])
        if policy.get("State") != "ENABLED" or not targets or not details.get("Schedules"):
            continue
        resources = details.get("ResourceTypes", [])
        for volume in volumes:
            tags = instance.get("Tags", []) if "INSTANCE" in resources else volume.get("Tags", [])
            if ("INSTANCE" in resources or "VOLUME" in resources) and any(
                t in tags for t in targets
            ):
                covered.add(volume["VolumeId"])
    checks.append(
        (
            "enabled tagged DLM policy covers all EBS volumes",
            bool(volume_ids) and set(volume_ids) <= covered,
        )
    )
    return checks


def main() -> int:
    if not sys.stdin.isatty():
        print("FAIL: run interactively in CloudShell")
        return 1
    print("READ ONLY: Mumbai EC2, security groups, EBS, DLM, Elastic IP and SSM.")
    print("No changes; identifiers and AWS response bodies will not be displayed.")
    try:
        checks = verify(getpass.getpass("Instance ID (hidden): ").strip())
        for label, passed in checks:
            print(("PASS: " if passed else "FAIL: ") + label)
        return 0 if all(passed for _, passed in checks) else 1
    except (CheckFailure, KeyError, TypeError, KeyboardInterrupt, EOFError):
        print("FAIL: unable to verify; check input, region, CLI and read permissions")
        return 1
    except Exception:
        print("FAIL: unexpected response; no AWS diagnostic values displayed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
