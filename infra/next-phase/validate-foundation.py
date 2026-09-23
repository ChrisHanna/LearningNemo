#!/usr/bin/env python3
"""Fail closed if the WP1 ARM template gains unapproved resource types."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ALLOWED_RESOURCE_TYPES = {
    "Microsoft.Network/networkSecurityGroups",
    "Microsoft.Network/routeTables",
    "Microsoft.Network/virtualNetworks",
    "Microsoft.Resources/deployments",
    "Microsoft.Resources/resourceGroups",
}
REQUIRED_RULE_PROPERTIES = {
    "deny-all-inbound": {
        "access": "Deny",
        "direction": "Inbound",
        "protocol": "*",
        "destinationAddressPrefix": "*",
        "destinationPortRange": "*",
    },
    "deny-trusted-platform-outbound": {
        "access": "Deny",
        "direction": "Outbound",
    },
    "deny-saw-east-west": {
        "access": "Deny",
        "direction": "Outbound",
    },
    "deny-direct-azure-sql": {
        "access": "Deny",
        "direction": "Outbound",
        "protocol": "*",
        "destinationAddressPrefix": "Sql",
        "destinationPortRange": "*",
    },
}
FORBIDDEN_RESOURCE_FRAGMENTS = {
    "azureFirewalls",
    "containerApps",
    "managedEnvironments",
    "natGateways",
    "networkInterfaces",
    "publicIPAddresses",
    "servers/databases",
    "virtualMachines",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    return parser.parse_args()


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


def validate_template(template: dict[str, Any]) -> tuple[list[str], set[str]]:
    resources = resources_in(template)
    resource_types = {str(resource.get("type", "")) for resource in resources}
    disallowed = sorted(resource_types - ALLOWED_RESOURCE_TYPES)
    forbidden = sorted(
        resource_type
        for resource_type in resource_types
        if any(fragment.casefold() in resource_type.casefold() for fragment in FORBIDDEN_RESOURCE_FRAGMENTS)
    )
    rules: dict[str, dict[str, Any]] = {}
    for resource in resources:
        if resource.get("type") != "Microsoft.Network/networkSecurityGroups":
            continue
        for rule in ((resource.get("properties") or {}).get("securityRules") or []):
            if isinstance(rule, dict):
                rules[str(rule.get("name", ""))] = rule.get("properties") or {}
    changed_rules = sorted(
        name
        for name, expected in REQUIRED_RULE_PROPERTIES.items()
        if name not in rules or any(rules[name].get(key) != value for key, value in expected.items())
    )

    failures: list[str] = []
    expected_schema = "https://schema.management.azure.com/schemas/2018-05-01/subscriptionDeploymentTemplate.json#"
    if template.get("$schema") != expected_schema:
        failures.append("compiled template uses an unexpected subscription deployment schema")
    if template.get("contentVersion") != "1.0.0.0":
        failures.append("compiled template contentVersion differs")
    if disallowed:
        failures.append(f"unapproved resource types: {', '.join(disallowed)}")
    if forbidden:
        failures.append(f"metered resource type detected: {', '.join(forbidden)}")
    if changed_rules:
        failures.append(f"required deny rules missing or changed: {', '.join(changed_rules)}")
    return failures, resource_types


def main() -> int:
    args = parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read compiled template: {error}", file=sys.stderr)
        return 1

    failures, resource_types = validate_template(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1

    print(f"PASS compiled template uses {len(resource_types)} approved resource types")
    print("PASS no metered compute, firewall, public IP, NAT gateway, or database resource is present")
    print("PASS required SAW deny rules are present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())