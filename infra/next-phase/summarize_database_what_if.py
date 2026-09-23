#!/usr/bin/env python3
"""Print identifier-free resource-type counts for WP3 what-if results."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


KNOWN_TYPES = (
    "Microsoft.Authorization/roleAssignments",
    "Microsoft.ContainerRegistry/registries",
    "Microsoft.Resources/resourceGroups",
    "Microsoft.Resources/deployments",
    "Microsoft.ManagedIdentity/userAssignedIdentities",
    "Microsoft.Sql/servers/databases",
    "Microsoft.Sql/servers",
    "Microsoft.Network/privateDnsZones/virtualNetworkLinks",
    "Microsoft.Network/privateDnsZones",
    "Microsoft.Network/privateEndpoints/privateDnsZoneGroups",
    "Microsoft.Network/privateEndpoints",
    "Microsoft.App/jobs",
    "Microsoft.Compute/virtualMachines/runCommands",
    "Microsoft.Compute/virtualMachines",
    "Microsoft.Network/networkInterfaces",
    "Microsoft.Network/networkSecurityGroups/securityRules",
    "Microsoft.Network/natGateways",
    "Microsoft.Network/publicIPAddresses",
    "Microsoft.Network/virtualNetworks/subnets",
)


def resource_type(resource_id: str) -> str:
    normalized = resource_id.casefold()
    parts = resource_id.strip("/").split("/")
    lowered = [part.casefold() for part in parts]
    if "providers" in lowered:
        index = len(lowered) - 1 - lowered[::-1].index("providers")
        resource_parts = parts[index + 1 :]
        if len(resource_parts) >= 2:
            candidate = f"{resource_parts[0]}/{'/'.join(resource_parts[1::2])}"
            canonical = next(
                (item for item in KNOWN_TYPES if item.casefold() == candidate.casefold()),
                None,
            )
            if canonical is not None:
                return canonical
    if "resourcegroups" in lowered:
        return "Microsoft.Resources/resourceGroups"
    for candidate in sorted(KNOWN_TYPES, key=len, reverse=True):
        if candidate.casefold() in normalized:
            return candidate
    return "unknown"


def change_resource_type(change: dict[str, object]) -> str:
    for container_name in ("after", "before", "targetResource"):
        container = change.get(container_name)
        if not isinstance(container, dict):
            continue
        explicit = container.get("type") or container.get("resourceType")
        if isinstance(explicit, str):
            canonical = next(
                (item for item in KNOWN_TYPES if item.casefold() == explicit.casefold()),
                None,
            )
            if canonical is not None:
                return canonical
    return resource_type(str(change.get("resourceId", "")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("what_if", type=Path)
    args = parser.parse_args()
    try:
        document = json.loads(args.what_if.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read database what-if result: {error}", file=sys.stderr)
        return 1
    changes = document.get("changes") or []
    counts = Counter(str(change.get("changeType", "Unknown")) for change in changes)
    print("Database what-if totals: " + (", ".join(f"{key}={counts[key]}" for key in sorted(counts)) or "none=0"))
    by_type = Counter(
        (str(change.get("changeType", "Unknown")), change_resource_type(change))
        for change in changes
    )
    for (change_type, resource_name), count in sorted(by_type.items()):
        print(f"  {change_type} {resource_name}={count}")
    if counts.get("Delete", 0):
        print("FAIL database what-if contains delete operations", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())