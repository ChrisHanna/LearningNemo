#!/usr/bin/env python3
"""Fail closed if the WP2a template gains workloads or paid dependencies."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ALLOWED_RESOURCE_TYPES = {
    "Microsoft.App/managedEnvironments",
}
FORBIDDEN_TYPE_FRAGMENTS = {
    "containerApps",
    "jobs",
    "operationalInsights",
    "registries",
    "servers/databases",
    "virtualMachines",
    "publicIPAddresses",
    "azureFirewalls",
    "natGateways",
}


def resources_in(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        resources = value.get("resources")
        if isinstance(resources, list):
            found.extend(item for item in resources if isinstance(item, dict))
        for child in value.values():
            found.extend(resources_in(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(resources_in(child))
    return found


def validate_template(template: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if template.get("$schema") != "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#":
        failures.append("compiled template uses an unexpected resource-group deployment schema")
    if template.get("contentVersion") != "1.0.0.0":
        failures.append("compiled template contentVersion differs")
    resources = resources_in(template)
    resource_types = {str(resource.get("type", "")) for resource in resources}
    disallowed = sorted(resource_types - ALLOWED_RESOURCE_TYPES)
    forbidden = sorted(
        resource_type
        for resource_type in resource_types
        if any(fragment.casefold() in resource_type.casefold() for fragment in FORBIDDEN_TYPE_FRAGMENTS)
    )
    if disallowed:
        failures.append(f"unapproved resource types: {', '.join(disallowed)}")
    if forbidden:
        failures.append(f"paid or workload resource type detected: {', '.join(forbidden)}")

    environments = [
        resource for resource in resources if resource.get("type") == "Microsoft.App/managedEnvironments"
    ]
    if len(environments) != 1:
        failures.append(f"expected one Container Apps environment, found {len(environments)}")
    runtime_tags = str((template.get("variables") or {}).get("runtimeTags", "")).casefold()
    for required_tag_value in (
        "vnet-integrated-runtime-envelope",
        "wp2a-runtime",
        "disposable",
        "expiresat",
        "monthlycostceiling",
    ):
        if required_tag_value not in runtime_tags:
            failures.append(f"runtime tag contract is missing: {required_tag_value}")
    if len(environments) == 1:
        properties = environments[0].get("properties") or {}
        expected = {
            "publicNetworkAccess": "Enabled",
            "zoneRedundant": False,
        }
        if any(properties.get(key) != value for key, value in expected.items()):
            failures.append("Container Apps cost or access profile differs")
        if "appLogsConfiguration" in properties:
            failures.append("Container Apps logging configuration must be omitted in WP2a")
        infrastructure_group = properties.get("infrastructureResourceGroup")
        normalized_group = infrastructure_group.casefold() if isinstance(infrastructure_group, str) else ""
        if not normalized_group or not (
            "mrg-" in normalized_group or "infrastructureresourcegroupname" in normalized_group
        ):
            failures.append("Container Apps managed infrastructure resource group is missing")
        vnet = properties.get("vnetConfiguration") or {}
        subnet_id = vnet.get("infrastructureSubnetId")
        normalized_subnet = subnet_id.casefold() if isinstance(subnet_id, str) else ""
        if (
            "microsoft.network/virtualnetworks/subnets" not in normalized_subnet
            or not (
                "snet-container-apps" in normalized_subnet
                or "containerappssubnetname" in normalized_subnet
            )
        ):
            failures.append("Container Apps environment must use the WP1 delegated subnet")
        if vnet.get("internal") is not False:
            failures.append("Container Apps environment must use external VNet-integrated ingress in WP2a")
        if ((properties.get("peerAuthentication") or {}).get("mtls") or {}).get("enabled") is not True:
            failures.append("Container Apps peer mTLS must be enabled")
        if ((properties.get("peerTrafficConfiguration") or {}).get("encryption") or {}).get("enabled") is not True:
            failures.append("Container Apps peer traffic encryption must be enabled")
        profiles = properties.get("workloadProfiles") or []
        if profiles != [{"name": "Consumption", "workloadProfileType": "Consumption"}]:
            failures.append("Container Apps environment must contain only the Consumption profile")
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read compiled platform template: {error}", file=sys.stderr)
        return 1
    failures = validate_template(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS runtime template contains exactly one empty Consumption environment")
    print("PASS no apps, jobs, persisted logs, registry, SQL, VM, firewall, or NAT gateway is present")
    print("PASS environment uses the WP1 delegated subnet and a named managed infrastructure group")
    print("PASS peer mTLS and peer traffic encryption are enabled; identities remain a separate IaC slice")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())