#!/usr/bin/env python3
"""Verify deployed WP2a resources without printing Azure identity identifiers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from runtime_parameters import ParameterError
from runtime_parameters import parameter_values
from platform_contract import IDENTITY_PURPOSES
from platform_contract import identity_resource_tags
from platform_contract import infrastructure_resource_group_name
from platform_contract import platform_group_tags
from platform_contract import platform_inventory
from platform_contract import resource_identity
from platform_contract import runtime_resource_tags
from workload_contract import workload_inventory


IDENTITY_API_VERSION = "2024-11-30"
ENVIRONMENT_API_VERSION = "2026-01-01"
class VerificationError(RuntimeError):
    pass


def az_json(arguments: list[str], *, timeout: int = 60) -> Any:
    try:
        result = subprocess.run(
            ["az", *arguments, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VerificationError("Azure state query failed or timed out") from error
    if result.returncode != 0:
        raise VerificationError("Azure state query failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise VerificationError("Azure state query returned unreadable JSON") from error


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def tags_match(actual: Any, expected: dict[str, str]) -> bool:
    return isinstance(actual, dict) and all(actual.get(key) == value for key, value in expected.items())


def normalized_location(value: Any) -> str:
    return "".join(str(value).split()).casefold()


def resource_show(resource_group: str, name: str, resource_type: str, api_version: str) -> dict[str, Any]:
    result = az_json(
        [
            "resource",
            "show",
            "--resource-group",
            resource_group,
            "--name",
            name,
            "--resource-type",
            resource_type,
            "--api-version",
            api_version,
        ]
    )
    require(isinstance(result, dict), "Azure resource query returned an unexpected shape")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--allow-workloads", action="store_true")
    return parser.parse_args()


def expected_inventory(values: dict[str, Any], *, allow_workloads: bool) -> set[tuple[str, str]]:
    expected = platform_inventory(values)
    return expected | workload_inventory(values) if allow_workloads else expected


def main() -> int:
    args = parse_args()
    try:
        try:
            values = parameter_values(args.parameters, allow_runtime_expiry=False)
        except ParameterError as error:
            raise VerificationError(str(error)) from error
        account = az_json(["account", "show"], timeout=20)
        expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
        if expected_subscription:
            require(account.get("id") == expected_subscription, "active subscription mismatch")

        project = values["projectName"]
        environment = values["environment"]
        resource_group = values["platformResourceGroupName"]
        group = az_json(["group", "show", "--name", resource_group], timeout=20)
        group_tags = platform_group_tags(values)
        require(group.get("location") == values["location"], "platform resource group location differs")
        require(tags_match(group.get("tags"), group_tags), "platform resource group tags differ")

        identity_tags = identity_resource_tags(values)
        runtime_tags = runtime_resource_tags(values)
        resources = az_json(["resource", "list", "--resource-group", resource_group], timeout=30)
        actual = {
            resource_identity(item.get("type", ""), item.get("name", ""))
            for item in resources
        }
        require(
            actual == expected_inventory(values, allow_workloads=args.allow_workloads),
            "platform resource inventory differs from the selected WP1/WP2a/WP2b contract",
        )

        for suffix, purpose in IDENTITY_PURPOSES.items():
            identity = resource_show(
                resource_group,
                f"id-{project}-{suffix}-{environment}",
                "Microsoft.ManagedIdentity/userAssignedIdentities",
                IDENTITY_API_VERSION,
            )
            require(identity.get("location") == values["location"], f"{suffix} identity location differs")
            require(tags_match(identity.get("tags"), {**identity_tags, "identityPurpose": purpose}), f"{suffix} identity tags differ")
            require((identity.get("properties") or {}).get("isolationScope") == "Regional", f"{suffix} identity isolation differs")

        managed_environment = resource_show(
            resource_group,
            values["containerAppsEnvironmentName"],
            "Microsoft.App/managedEnvironments",
            ENVIRONMENT_API_VERSION,
        )
        require(
            normalized_location(managed_environment.get("location"))
            == normalized_location(values["location"]),
            "Container Apps environment location differs",
        )
        require(tags_match(managed_environment.get("tags"), runtime_tags), "Container Apps environment tags differ")
        properties = managed_environment.get("properties") or {}
        app_logs = properties.get("appLogsConfiguration") or {}
        require(app_logs.get("destination") in {None, ""}, "persistent app logs are enabled")
        require(not app_logs.get("logAnalyticsConfiguration"), "Log Analytics configuration is present")
        require(properties.get("publicNetworkAccess") == "Enabled", "Container Apps public access mode differs")
        require(properties.get("zoneRedundant") is False, "Container Apps zone redundancy differs")
        expected_managed_group = infrastructure_resource_group_name(values)
        require(
            properties.get("infrastructureResourceGroup") == expected_managed_group,
            "Container Apps managed infrastructure group differs",
        )
        require(
            ((properties.get("peerAuthentication") or {}).get("mtls") or {}).get("enabled") is True,
            "Container Apps peer mTLS is disabled",
        )
        require(
            ((properties.get("peerTrafficConfiguration") or {}).get("encryption") or {}).get("enabled") is True,
            "Container Apps peer traffic encryption is disabled",
        )
        vnet = properties.get("vnetConfiguration") or {}
        subscription_id = str(account.get("id", ""))
        require(bool(subscription_id), "active subscription identifier is unavailable")
        expected_subnet_id = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/"
            f"Microsoft.Network/virtualNetworks/vnet-{project}-platform-{environment}/"
            "subnets/snet-container-apps"
        )
        require(
            str(vnet.get("infrastructureSubnetId", "")).casefold() == expected_subnet_id.casefold(),
            "Container Apps delegated subnet differs",
        )
        require(vnet.get("internal") is False, "Container Apps environment must remain external in WP2a")
        profiles = properties.get("workloadProfiles") or []
        profile_shape = [
            {"name": profile.get("name"), "workloadProfileType": profile.get("workloadProfileType")}
            for profile in profiles
            if isinstance(profile, dict)
        ]
        require(
            profile_shape == [{"name": "Consumption", "workloadProfileType": "Consumption"}],
            "Container Apps workload profile differs",
        )

        managed_group = az_json(["group", "show", "--name", expected_managed_group], timeout=20)
        require(tags_match(managed_group.get("tags"), runtime_tags), "managed infrastructure group tags differ")
        managed_resources = az_json(
            ["resource", "list", "--resource-group", expected_managed_group],
            timeout=30,
        )
        managed_types = [str(item.get("type", "")).casefold() for item in managed_resources]
        require(
            managed_types.count("microsoft.network/loadbalancers") == 1,
            "managed infrastructure load balancer inventory differs",
        )
        require(
            managed_types.count("microsoft.network/publicipaddresses") == 1,
            "managed infrastructure public IP inventory differs",
        )
        require(len(managed_types) == 2, "managed infrastructure contains an unexpected resource")

        print("PASS six regional service identities and purpose tags match")
        print("PASS VNet-integrated external Consumption environment controls match")
        if args.allow_workloads:
            print("PASS exact platform inventory contains only WP1, WP2a, and four expected WP2b apps")
        else:
            print("PASS exact platform inventory contains no app workload or deferred dependency")
        print("PASS Azure-managed load balancer and public IP cost resources are accounted for")
        print("PASS deployed WP2a verification complete")
        return 0
    except VerificationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())