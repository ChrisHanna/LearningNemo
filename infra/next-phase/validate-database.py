#!/usr/bin/env python3
"""Validate compiled WP3 Azure SQL base and private-network templates."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


BASE_TYPES = Counter(
    {
        "Microsoft.ManagedIdentity/userAssignedIdentities": 1,
        "Microsoft.Sql/servers": 1,
        "Microsoft.Sql/servers/databases": 1,
    }
)
NETWORK_TYPES = Counter(
    {
        "Microsoft.Network/privateDnsZones": 1,
        "Microsoft.Network/privateDnsZones/virtualNetworkLinks": 1,
        "Microsoft.Network/privateEndpoints": 1,
        "Microsoft.Network/privateEndpoints/privateDnsZoneGroups": 1,
    }
)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"unable to read compiled template {path.name}") from error
    if not isinstance(value, dict):
        raise ValueError("compiled database template must be an object")
    return value


def nested_resources(template: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for resource in template.get("resources") or []:
        if not isinstance(resource, dict):
            continue
        nested = ((resource.get("properties") or {}).get("template") or {}).get("resources")
        if isinstance(nested, list):
            result.extend(item for item in nested if isinstance(item, dict))
    return result


def validate_base(template: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    root_resources = template.get("resources") or []
    root_types = Counter(str(item.get("type", "")) for item in root_resources if isinstance(item, dict))
    if root_types != Counter(
        {
            "Microsoft.Resources/resourceGroups": 1,
            "Microsoft.Resources/deployments": 1,
        }
    ):
        failures.append("database subscription resource inventory differs")
    resources = nested_resources(template)
    if Counter(str(item.get("type", "")) for item in resources) != BASE_TYPES:
        failures.append("database base resource inventory differs")
        return failures
    identity = next(item for item in resources if item.get("type") == "Microsoft.ManagedIdentity/userAssignedIdentities")
    if (identity.get("properties") or {}).get("isolationScope") != "Regional":
        failures.append("database bootstrap identity must use regional isolation")
    if "database-bootstrap-admin" not in str(identity.get("tags", "")):
        failures.append("database bootstrap identity purpose tag is missing")

    server = next(item for item in resources if item.get("type") == "Microsoft.Sql/servers")
    server_properties = server.get("properties") or {}
    administrator = server_properties.get("administrators") or {}
    if (
        (server.get("identity") or {}).get("type") != "UserAssigned"
        or len((server.get("identity") or {}).get("userAssignedIdentities") or {}) != 1
        or administrator.get("administratorType") != "ActiveDirectory"
        or administrator.get("principalType") != "Application"
        or administrator.get("azureADOnlyAuthentication") is not True
        or server_properties.get("publicNetworkAccess") != "Disabled"
        or server_properties.get("minimalTlsVersion") != "1.2"
        or server_properties.get("restrictOutboundNetworkAccess") != "Enabled"
        or server_properties.get("isIPv6Enabled") != "Disabled"
    ):
        failures.append("SQL server identity, Entra-only, TLS, or network controls differ")
    if "administratorLogin" in server_properties or "administratorLoginPassword" in server_properties:
        failures.append("SQL authentication is present")

    database = next(item for item in resources if item.get("type") == "Microsoft.Sql/servers/databases")
    sku = database.get("sku") or {}
    properties = database.get("properties") or {}
    if (
        sku.get("tier") != "GeneralPurpose"
        or sku.get("family") != "Gen5"
        or "GP_S_Gen5_" not in str(sku.get("name"))
        or properties.get("useFreeLimit") != "[parameters('useFreeLimit')]"
        or properties.get("freeLimitExhaustionBehavior") != "[parameters('freeLimitExhaustionBehavior')]"
        or properties.get("autoPauseDelay") != "[parameters('autoPauseDelayMinutes')]"
        or properties.get("minCapacity") != "[json(parameters('minVcores'))]"
        or properties.get("requestedBackupStorageRedundancy") != "Local"
        or properties.get("readScale") != "Disabled"
        or properties.get("zoneRedundant") is not False
        or properties.get("highAvailabilityReplicaCount") != 0
        or properties.get("isLedgerOn") is not False
    ):
        failures.append("free-limit serverless database controls differ")
    serialized = json.dumps(template, sort_keys=True).casefold()
    for required in (
        "azure-sql-free-serverless",
        "auto-pause-on-exhaustion",
        "wp3-database",
    ):
        if required not in serialized:
            failures.append(f"database tag contract is missing: {required}")
    for prohibited in (
        "microsoft.network/privateendpoints",
        "microsoft.operationalinsights",
        "microsoft.insights",
        "microsoft.storage",
    ):
        if prohibited in serialized:
            failures.append(f"database base contains deferred resource: {prohibited}")
    return failures


def validate_network(template: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    resources = template.get("resources") or []
    if Counter(str(item.get("type", "")) for item in resources if isinstance(item, dict)) != NETWORK_TYPES:
        failures.append("database network resource inventory differs")
        return failures
    endpoint = next(item for item in resources if item.get("type") == "Microsoft.Network/privateEndpoints")
    connection = (((endpoint.get("properties") or {}).get("privateLinkServiceConnections") or [{}])[0].get("properties") or {})
    if connection.get("groupIds") != ["sqlServer"] or "Microsoft.Sql/servers" not in str(connection.get("privateLinkServiceId", "")):
        failures.append("private endpoint is not bound to the SQL server group")
    zone = next(item for item in resources if item.get("type") == "Microsoft.Network/privateDnsZones")
    if "sqlServerHostname" not in str((template.get("variables") or {}).get("sqlPrivateDnsZoneName", "")):
        failures.append("SQL private DNS zone is not Azure-environment aware")
    if zone.get("location") != "global":
        failures.append("SQL private DNS zone must use global location")
    link = next(item for item in resources if item.get("type") == "Microsoft.Network/privateDnsZones/virtualNetworkLinks")
    if (link.get("properties") or {}).get("registrationEnabled") is not False:
        failures.append("SQL private DNS registration must be disabled")
    serialized = json.dumps(template, sort_keys=True).casefold()
    for required in (
        "expiring-sql-private-endpoint",
        "wp3-database-network",
        "expiresat",
        "snet-private-endpoints",
    ):
        if required not in serialized:
            failures.append(f"database network contract is missing: {required}")
    for prohibited in (
        "microsoft.network/publicipaddresses",
        "microsoft.network/natgateways",
        "microsoft.compute",
        "microsoft.app",
    ):
        if prohibited in serialized:
            failures.append(f"database network contains prohibited resource: {prohibited}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("network", type=Path)
    args = parser.parse_args()
    try:
        failures = [*validate_base(load(args.base)), *validate_network(load(args.network))]
    except ValueError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS SQL base is Entra-only, public-disabled, TLS 1.2, and free-limit serverless")
    print("PASS database bootstrap identity is distinct and no SQL password exists")
    print("PASS private endpoint and cloud-aware DNS are isolated in an expiring overlay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())