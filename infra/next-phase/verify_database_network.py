#!/usr/bin/env python3
"""Verify the deployed SQL private endpoint and DNS overlay without printing identifiers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from database_network_contract import classify
from database_network_contract import group_tags
from database_network_contract import is_service_managed_network_interface
from database_network_contract import private_dns_zone_name
from database_network_contract import private_endpoint_name
from database_network_contract import resource_tags
from database_network_contract import tags_match
from database_network_parameters import DatabaseNetworkParameterError
from database_network_parameters import parameter_values


class DatabaseNetworkVerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DatabaseNetworkVerificationError(message)


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
        raise DatabaseNetworkVerificationError("database network verification failed") from error
    if result.returncode != 0:
        raise DatabaseNetworkVerificationError("database network verification query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise DatabaseNetworkVerificationError("database network verification returned unreadable JSON") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    args = parser.parse_args()
    try:
        try:
            values = parameter_values(args.parameters)
        except DatabaseNetworkParameterError as error:
            raise DatabaseNetworkVerificationError(str(error)) from error
        account = az_json(["account", "show"], timeout=20)
        expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
        require(not expected_subscription or account.get("id") == expected_subscription, "active subscription mismatch")
        suffix = str(((account.get("environment") or {}).get("suffixes") or {}).get("sqlServerHostname", ""))
        if not suffix:
            suffix = ".database.windows.net"
        zone_name = private_dns_zone_name(suffix)
        group_name = values["databaseNetworkResourceGroupName"]
        group = az_json(["group", "show", "--name", group_name], timeout=20)
        require(group.get("location") == values["location"], "database network group location differs")
        require(tags_match(group.get("tags"), group_tags(values)), "database network group tags differ")
        resources = az_json(["resource", "list", "--resource-group", group_name], timeout=30)
        require(classify(resources) == "complete", "database network inventory differs")
        for resource in resources:
            if is_service_managed_network_interface(resource):
                continue
            require(tags_match(resource.get("tags"), resource_tags(values)), "database network tags differ")

        endpoint_name = private_endpoint_name(values)
        endpoint = az_json(
            ["network", "private-endpoint", "show", "--resource-group", group_name, "--name", endpoint_name],
            timeout=45,
        )
        expected_subnet = (
            f"/resourceGroups/{values['platformResourceGroupName']}/providers/Microsoft.Network/"
            f"virtualNetworks/{values['platformVnetName']}/subnets/{values['privateEndpointSubnetName']}"
        ).casefold()
        require(str((endpoint.get("subnet") or {}).get("id", "")).casefold().endswith(expected_subnet), "private endpoint subnet differs")
        interfaces = endpoint.get("networkInterfaces") or []
        inventory_interfaces = [item for item in resources if is_service_managed_network_interface(item)]
        require(
            len(interfaces) == 1
            and len(inventory_interfaces) == 1
            and str(interfaces[0].get("id", "")).casefold()
            == str(inventory_interfaces[0].get("id", "")).casefold(),
            "private endpoint network interface differs",
        )
        connections = endpoint.get("privateLinkServiceConnections") or []
        require(len(connections) == 1, "private endpoint connection count differs")
        connection = connections[0].get("privateLinkServiceConnectionState") or {}
        expected_server = (
            f"/resourceGroups/{values['databaseResourceGroupName']}/providers/Microsoft.Sql/servers/"
            f"{values['sqlServerName']}"
        ).casefold()
        require(
            (connections[0].get("groupIds") or []) == ["sqlServer"]
            and str(connections[0].get("privateLinkServiceId", "")).casefold().endswith(expected_server)
            and connection.get("status") == "Approved",
            "SQL private link is not approved for the exact server",
        )

        link = az_json(
            [
                "network",
                "private-dns",
                "link",
                "vnet",
                "show",
                "--resource-group",
                group_name,
                "--zone-name",
                zone_name,
                "--name",
                f"link-{values['projectName']}-{values['environment']}",
            ],
            timeout=30,
        )
        expected_vnet = (
            f"/resourceGroups/{values['platformResourceGroupName']}/providers/Microsoft.Network/"
            f"virtualNetworks/{values['platformVnetName']}"
        ).casefold()
        require(link.get("registrationEnabled") is False, "private DNS registration is enabled")
        require(str((link.get("virtualNetwork") or {}).get("id", "")).casefold().endswith(expected_vnet), "private DNS VNet link differs")
        zone_group = az_json(
            [
                "network",
                "private-endpoint",
                "dns-zone-group",
                "show",
                "--resource-group",
                group_name,
                "--endpoint-name",
                endpoint_name,
                "--name",
                "default",
            ],
            timeout=30,
        )
        zone_configs = zone_group.get("privateDnsZoneConfigs") or []
        require(
            len(zone_configs) == 1
            and str((zone_configs[0].get("privateDnsZoneId") or "")).casefold().endswith(
                f"/resourcegroups/{group_name}/providers/microsoft.network/privatednszones/{zone_name}".casefold()
            ),
            "private endpoint DNS zone group differs",
        )
        record = az_json(
            [
                "network",
                "private-dns",
                "record-set",
                "a",
                "show",
                "--resource-group",
                group_name,
                "--zone-name",
                zone_name,
                "--name",
                values["sqlServerName"],
            ],
            timeout=30,
        )
        require(len(record.get("aRecords") or []) == 1, "SQL private DNS A record differs")
        print("PASS exact expiring WP3 database network inventory and ownership tags match")
        print("PASS SQL private link is approved in the dedicated eastus subnet")
        print("PASS private DNS is linked to the trusted VNet with one SQL A record")
        return 0
    except DatabaseNetworkVerificationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())