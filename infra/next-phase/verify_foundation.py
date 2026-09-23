#!/usr/bin/env python3
"""Verify deployed WP1 Azure state without printing resource identifiers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from foundation_parameters import ParameterError
from foundation_parameters import parameter_values


class VerificationError(RuntimeError):
    pass


def az_json(arguments: list[str], *, timeout: int = 45) -> Any:
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


def subnet_by_name(vnet: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [subnet for subnet in vnet.get("subnets") or [] if subnet.get("name") == name]
    require(len(matches) == 1, f"expected exactly one {name} subnet")
    return matches[0]


def linked_resource_name(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("id", "")).rstrip("/").split("/")[-1]


def verify_resources(resource_group: str, expected: set[tuple[str, str]], tags: dict[str, str]) -> None:
    resources = az_json(["resource", "list", "--resource-group", resource_group])
    actual = {
        (str(item.get("type", "")).casefold(), str(item.get("name", "")).casefold()): item
        for item in resources
    }
    normalized_expected = {(resource_type.casefold(), name.casefold()) for resource_type, name in expected}
    require(normalized_expected <= set(actual), f"foundation resource inventory differs in {resource_group}")
    require(
        all(tags_match(actual[identity].get("tags"), tags) for identity in normalized_expected),
        f"foundation resource tags differ in {resource_group}",
    )


def verify_nsg(resource_group: str, name: str) -> None:
    nsg = az_json(["network", "nsg", "show", "--resource-group", resource_group, "--name", name])
    rules = {rule.get("name"): rule for rule in nsg.get("securityRules") or []}
    expected = {
        "deny-all-inbound": {
            "access": "Deny",
            "direction": "Inbound",
            "protocol": "*",
            "destinationAddressPrefix": "*",
            "destinationPortRange": "*",
        },
        "deny-trusted-platform-outbound": {"access": "Deny", "direction": "Outbound"},
        "deny-saw-east-west": {"access": "Deny", "direction": "Outbound"},
        "deny-direct-azure-sql": {
            "access": "Deny",
            "direction": "Outbound",
            "protocol": "*",
            "destinationAddressPrefix": "Sql",
            "destinationPortRange": "*",
        },
    }
    require(set(rules) == set(expected), "SAW NSG custom rule inventory differs")
    for name, properties in expected.items():
        require(
            all(rules[name].get(key) == value for key, value in properties.items()),
            f"SAW NSG rule differs: {name}",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        try:
            values = parameter_values(args.parameters, allow_runtime_expiry=False)
        except ParameterError as error:
            raise VerificationError(str(error)) from error
        expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
        account = az_json(["account", "show"], timeout=20)
        if expected_subscription:
            require(account.get("id") == expected_subscription, "active subscription mismatch")

        project = values["projectName"]
        environment = values["environment"]
        platform_group = values["platformResourceGroupName"]
        saw_group = values["sawResourceGroupName"]
        platform_vnet_name = f"vnet-{project}-platform-{environment}"
        saw_vnet_name = f"vnet-{project}-saw-{environment}"
        platform_nsg_name = f"nsg-{platform_vnet_name}-container-apps"
        saw_nsg_name = f"nsg-{saw_vnet_name}-workspace"
        route_table_name = f"rt-{saw_vnet_name}-workspace"
        common_tags = {
            "project": project,
            "environment": environment,
            "managedBy": "bicep",
            "owner": values["ownerTag"],
            "costProfile": "low-cost-poc",
            **values["additionalTags"],
        }
        platform_tags = {**common_tags, "trustZone": "trusted-platform", "disposable": "false"}
        saw_tags = {
            **common_tags,
            "trustZone": "untrusted-agent-workspace",
            "disposable": "true",
            "expiresOn": values["expiresOn"],
            "sawMaturity": "network-foundation-only",
        }

        platform_rg = az_json(["group", "show", "--name", platform_group])
        saw_rg = az_json(["group", "show", "--name", saw_group])
        require(platform_rg.get("location") == values["location"], "platform resource group location differs")
        require(saw_rg.get("location") == values["location"], "SAW resource group location differs")
        require(tags_match(platform_rg.get("tags"), platform_tags), "platform resource group tags differ")
        require(tags_match(saw_rg.get("tags"), saw_tags), "SAW resource group tags differ")

        verify_resources(
            platform_group,
            {
                ("Microsoft.Network/networkSecurityGroups", platform_nsg_name),
                ("Microsoft.Network/virtualNetworks", platform_vnet_name),
            },
            common_tags,
        )
        verify_resources(
            saw_group,
            {
                ("Microsoft.Network/networkSecurityGroups", saw_nsg_name),
                ("Microsoft.Network/routeTables", route_table_name),
                ("Microsoft.Network/virtualNetworks", saw_vnet_name),
            },
            saw_tags,
        )

        platform_vnet = az_json(
            ["network", "vnet", "show", "--resource-group", platform_group, "--name", platform_vnet_name]
        )
        require(
            (platform_vnet.get("addressSpace") or {}).get("addressPrefixes")
            == [values["platformVnetAddressPrefix"]],
            "platform VNet address space differs",
        )
        container_subnet = subnet_by_name(platform_vnet, "snet-container-apps")
        private_subnet = subnet_by_name(platform_vnet, "snet-private-endpoints")
        require(container_subnet.get("addressPrefix") == values["containerAppsSubnetPrefix"], "Container Apps CIDR differs")
        delegations = container_subnet.get("delegations") or []
        require(
            len(delegations) == 1 and delegations[0].get("serviceName") == "Microsoft.App/environments",
            "Container Apps delegation differs",
        )
        require(linked_resource_name(container_subnet.get("networkSecurityGroup")) == platform_nsg_name, "platform NSG link differs")
        require(private_subnet.get("addressPrefix") == values["privateEndpointSubnetPrefix"], "private endpoint CIDR differs")
        require(private_subnet.get("privateEndpointNetworkPolicies") == "Disabled", "private endpoint policy differs")

        saw_vnet = az_json(["network", "vnet", "show", "--resource-group", saw_group, "--name", saw_vnet_name])
        require(
            (saw_vnet.get("addressSpace") or {}).get("addressPrefixes") == [values["sawVnetAddressPrefix"]],
            "SAW VNet address space differs",
        )
        workspace_subnet = subnet_by_name(saw_vnet, "snet-workspace")
        firewall_subnet = subnet_by_name(saw_vnet, "AzureFirewallSubnet")
        require(workspace_subnet.get("addressPrefix") == values["sawWorkspaceSubnetPrefix"], "SAW workspace CIDR differs")
        require(linked_resource_name(workspace_subnet.get("networkSecurityGroup")) == saw_nsg_name, "SAW NSG link differs")
        require(linked_resource_name(workspace_subnet.get("routeTable")) == route_table_name, "SAW route table link differs")
        require(firewall_subnet.get("addressPrefix") == values["sawFirewallSubnetPrefix"], "firewall CIDR differs")
        require(not (saw_vnet.get("virtualNetworkPeerings") or []), "SAW VNet must not have peerings")
        verify_nsg(saw_group, saw_nsg_name)

        route_table = az_json(
            ["network", "route-table", "show", "--resource-group", saw_group, "--name", route_table_name]
        )
        require(route_table.get("disableBgpRoutePropagation") is True, "BGP route propagation is enabled")
        require(not (route_table.get("routes") or []), "foundation route table must not contain routes")

        print("PASS deployed foundation resource inventory and tags match")
        print("PASS platform and SAW network topology matches")
        print("PASS SAW deny rules, no peering, and empty route table match")
        print("PASS deployed foundation verification complete")
        return 0
    except VerificationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())