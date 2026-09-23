#!/usr/bin/env python3
"""Read-only Azure and network preflight for the next-phase foundation."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from foundation_parameters import ParameterError
from foundation_parameters import parameter_values


PROVIDER_CONTRACT = Path(__file__).with_name("provider-phases.json")
CONTAINER_APPS_RESERVED = (
    "169.254.0.0/16",
    "172.30.0.0/16",
    "172.31.0.0/16",
    "192.0.2.0/24",
    "100.100.0.0/17",
    "100.100.128.0/19",
    "100.100.160.0/19",
    "100.100.192.0/19",
)
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._()\-]{1,90}$")


class PreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class NetworkConfig:
    platform_vnet: ipaddress.IPv4Network
    container_apps_subnet: ipaddress.IPv4Network
    private_endpoint_subnet: ipaddress.IPv4Network
    saw_vnet: ipaddress.IPv4Network
    saw_workspace_subnet: ipaddress.IPv4Network
    saw_firewall_subnet: ipaddress.IPv4Network


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parameters",
        type=Path,
        default=Path(__file__).parent / "environments" / "dev.parameters.json",
    )
    parser.add_argument("--offline", action="store_true", help="Validate configuration without Azure calls.")
    parser.add_argument("--strict-future-providers", action="store_true")
    return parser.parse_args()


def apply_parameter_file(args: argparse.Namespace) -> None:
    try:
        values = parameter_values(args.parameters)
    except ParameterError as error:
        raise PreflightError(str(error)) from error
    mapping = {
        "location": "location",
        "project": "projectName",
        "environment": "environment",
        "platform_resource_group": "platformResourceGroupName",
        "saw_resource_group": "sawResourceGroupName",
        "platform_vnet": "platformVnetAddressPrefix",
        "container_apps_subnet": "containerAppsSubnetPrefix",
        "private_endpoint_subnet": "privateEndpointSubnetPrefix",
        "saw_vnet": "sawVnetAddressPrefix",
        "saw_workspace_subnet": "sawWorkspaceSubnetPrefix",
        "saw_firewall_subnet": "sawFirewallSubnetPrefix",
    }
    for attribute, parameter_name in mapping.items():
        setattr(args, attribute, values[parameter_name])


def ipv4_network(value: str, label: str) -> ipaddress.IPv4Network:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as error:
        raise PreflightError(f"{label} must be a canonical IPv4 CIDR: {error}") from error
    if not isinstance(network, ipaddress.IPv4Network):
        raise PreflightError(f"{label} must use IPv4")
    if not network.is_private:
        raise PreflightError(f"{label} must use private address space")
    return network


def validate_networks(args: argparse.Namespace) -> NetworkConfig:
    config = NetworkConfig(
        platform_vnet=ipv4_network(args.platform_vnet, "platform VNet"),
        container_apps_subnet=ipv4_network(args.container_apps_subnet, "Container Apps subnet"),
        private_endpoint_subnet=ipv4_network(args.private_endpoint_subnet, "private endpoint subnet"),
        saw_vnet=ipv4_network(args.saw_vnet, "SAW VNet"),
        saw_workspace_subnet=ipv4_network(args.saw_workspace_subnet, "SAW workspace subnet"),
        saw_firewall_subnet=ipv4_network(args.saw_firewall_subnet, "SAW firewall subnet"),
    )
    if config.platform_vnet.overlaps(config.saw_vnet):
        raise PreflightError("platform and SAW VNet address spaces overlap")
    for subnet, label in (
        (config.container_apps_subnet, "Container Apps subnet"),
        (config.private_endpoint_subnet, "private endpoint subnet"),
    ):
        if not subnet.subnet_of(config.platform_vnet):
            raise PreflightError(f"{label} is outside the platform VNet")
    for subnet, label in (
        (config.saw_workspace_subnet, "SAW workspace subnet"),
        (config.saw_firewall_subnet, "SAW firewall subnet"),
    ):
        if not subnet.subnet_of(config.saw_vnet):
            raise PreflightError(f"{label} is outside the SAW VNet")
    if config.container_apps_subnet.overlaps(config.private_endpoint_subnet):
        raise PreflightError("platform subnets overlap")
    if config.saw_workspace_subnet.overlaps(config.saw_firewall_subnet):
        raise PreflightError("SAW subnets overlap")
    if config.container_apps_subnet.prefixlen > 27:
        raise PreflightError("workload-profile Container Apps subnet must be /27 or larger")
    if config.saw_firewall_subnet.prefixlen > 26:
        raise PreflightError("AzureFirewallSubnet must be /26 or larger")
    for reserved_value in CONTAINER_APPS_RESERVED:
        reserved = ipaddress.ip_network(reserved_value)
        if config.container_apps_subnet.overlaps(reserved):
            raise PreflightError(f"Container Apps subnet overlaps reserved range {reserved}")
    return config


def validate_names(args: argparse.Namespace) -> None:
    for value, label in (
        (args.project, "project"),
        (args.environment, "environment"),
        (args.platform_resource_group, "platform resource group"),
        (args.saw_resource_group, "SAW resource group"),
    ):
        if not NAME_PATTERN.fullmatch(value):
            raise PreflightError(f"{label} contains unsupported characters or is too long")
    if args.platform_resource_group == args.saw_resource_group:
        raise PreflightError("platform and SAW resource groups must be different")


def az_json(arguments: list[str], *, timeout: int = 60) -> Any:
    try:
        result = subprocess.run(
            ["az", *arguments, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise PreflightError("an Azure CLI readiness check timed out") from error
    if result.returncode != 0:
        raise PreflightError("an Azure CLI readiness check failed; refresh `az login` and retry")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PreflightError("Azure CLI returned an unreadable readiness response") from error


def validate_account(location: str) -> None:
    account = az_json(["account", "show"], timeout=20)
    if account.get("environmentName") != "AzureCloud":
        raise PreflightError("the active Azure CLI cloud must be AzureCloud")
    expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
    if expected_subscription and account.get("id") != expected_subscription:
        raise PreflightError("the active subscription does not match AZURE_SUBSCRIPTION_ID")
    locations = az_json(["account", "list-locations"], timeout=30)
    if location not in {item.get("name") for item in locations}:
        raise PreflightError(f"location {location!r} is unavailable to the active subscription")


def validate_providers(strict_future: bool) -> None:
    try:
        contract = json.loads(PROVIDER_CONTRACT.read_text(encoding="utf-8"))
        phases = contract["phases"]
        if contract.get("schemaVersion") != 1 or not isinstance(phases, dict):
            raise ValueError
        current_providers = tuple(phases["foundation"])
        future_providers = tuple(
            sorted({name for phase, names in phases.items() if phase != "foundation" for name in names})
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise PreflightError("provider phase contract is invalid") from error
    providers = az_json(["provider", "list"], timeout=60)
    states = {item.get("namespace"): item.get("registrationState") for item in providers}
    missing_now = [name for name in current_providers if states.get(name) != "Registered"]
    missing_future = [name for name in future_providers if states.get(name) != "Registered"]
    if missing_now:
        raise PreflightError(f"register required provider(s): {', '.join(missing_now)}")
    print("PASS providers required by WP1 are registered")
    if missing_future:
        message = f"future providers not yet registered: {', '.join(missing_future)}"
        if strict_future:
            raise PreflightError(message)
        print(f"WARN {message}")
    else:
        print("PASS future-phase providers are registered")


def validate_skus(location: str) -> None:
    skus = az_json(
        [
            "vm",
            "list-skus",
            "--location",
            location,
            "--resource-type",
            "virtualMachines",
            "--all",
        ],
        timeout=90,
    )
    candidates = {"Standard_B2as_v2", "Standard_B4as_v2"}
    unrestricted = {
        item.get("name")
        for item in skus
        if item.get("name") in candidates and not item.get("restrictions")
    }
    if not unrestricted:
        raise PreflightError("neither Standard_B2as_v2 nor Standard_B4as_v2 is unrestricted")
    print(f"PASS available SAW VM candidates: {', '.join(sorted(unrestricted))}")


def validate_existing_vnets(args: argparse.Namespace, config: NetworkConfig) -> None:
    existing = az_json(["network", "vnet", "list"], timeout=45)
    expected = {
        (
            args.platform_resource_group.casefold(),
            f"vnet-{args.project}-platform-{args.environment}".casefold(),
        ): config.platform_vnet,
        (
            args.saw_resource_group.casefold(),
            f"vnet-{args.project}-saw-{args.environment}".casefold(),
        ): config.saw_vnet,
    }
    candidates = (config.platform_vnet, config.saw_vnet)
    conflicts: list[str] = []
    for vnet in existing:
        identity = (str(vnet.get("resourceGroup", "")).casefold(), str(vnet.get("name", "")).casefold())
        prefixes = ((vnet.get("addressSpace") or {}).get("addressPrefixes") or [])
        if identity in expected:
            if set(prefixes) != {str(expected[identity])}:
                conflicts.append("an expected VNet already exists with a different address prefix")
            continue
        for existing_value in prefixes:
            try:
                existing_network = ipaddress.ip_network(existing_value, strict=True)
            except ValueError:
                continue
            if any(candidate.overlaps(existing_network) for candidate in candidates):
                conflicts.append(f"candidate address space overlaps existing VNet prefix {existing_network}")
    if conflicts:
        raise PreflightError("; ".join(sorted(set(conflicts))))
    print("PASS candidate address spaces do not overlap existing VNets")


def classify_resource_inventory(
    actual: set[tuple[str, str]],
    expected: set[tuple[str, str]],
) -> str:
    normalized_actual = {(resource_type.casefold(), name.casefold()) for resource_type, name in actual}
    normalized_expected = {(resource_type.casefold(), name.casefold()) for resource_type, name in expected}
    if not normalized_actual:
        return "empty"
    if normalized_expected <= normalized_actual:
        return "complete"
    if normalized_actual < normalized_expected:
        return "partial"
    return "unexpected"


def validate_existing_foundation(args: argparse.Namespace) -> None:
    project = args.project
    environment = args.environment
    groups = {
        args.platform_resource_group: {
            ("Microsoft.Network/networkSecurityGroups", f"nsg-vnet-{project}-platform-{environment}-container-apps"),
            ("Microsoft.Network/virtualNetworks", f"vnet-{project}-platform-{environment}"),
        },
        args.saw_resource_group: {
            ("Microsoft.Network/networkSecurityGroups", f"nsg-vnet-{project}-saw-{environment}-workspace"),
            ("Microsoft.Network/routeTables", f"rt-vnet-{project}-saw-{environment}-workspace"),
            ("Microsoft.Network/virtualNetworks", f"vnet-{project}-saw-{environment}"),
        },
    }
    for resource_group, expected in groups.items():
        exists = az_json(["group", "exists", "--name", resource_group], timeout=20)
        if exists is False:
            print(f"PASS {resource_group} is absent and ready for first deployment")
            continue
        if exists is not True:
            raise PreflightError(f"could not determine whether {resource_group} exists")
        resources = az_json(["resource", "list", "--resource-group", resource_group], timeout=30)
        actual = {(str(item.get("type", "")), str(item.get("name", ""))) for item in resources}
        state = classify_resource_inventory(actual, expected)
        if state == "unexpected":
            raise PreflightError(f"{resource_group} contains resources outside the WP1 contract")
        if state in {"empty", "partial"}:
            print(f"WARN {resource_group} has {state} WP1 state; review what-if before rerun")
        else:
            print(f"PASS {resource_group} has a complete WP1 resource inventory")


def main() -> int:
    args = parse_args()
    try:
        apply_parameter_file(args)
        validate_names(args)
        config = validate_networks(args)
        print("PASS network names and CIDRs are structurally valid")
        if args.offline:
            print("PASS offline preflight complete; no Azure calls were made")
            return 0
        if shutil.which("az") is None:
            raise PreflightError("Azure CLI is not installed")
        validate_account(args.location)
        print(f"PASS active AzureCloud subscription supports {args.location}")
        validate_providers(args.strict_future_providers)
        validate_skus(args.location)
        validate_existing_vnets(args, config)
        validate_existing_foundation(args)
        print("PASS read-only Azure preflight complete")
        print("INFO no resources were created or changed")
        return 0
    except PreflightError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())