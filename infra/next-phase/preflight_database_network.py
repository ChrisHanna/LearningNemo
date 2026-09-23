#!/usr/bin/env python3
"""Read-only readiness checks for the expiring WP3 SQL network overlay."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from database_network_contract import classify
from database_network_contract import group_tags
from database_network_contract import is_service_managed_network_interface
from database_network_contract import resource_tags
from database_network_contract import tags_match
from database_network_parameters import DatabaseNetworkParameterError
from database_network_parameters import load_config
from database_network_parameters import parameter_values
from database_parameters import DatabaseParameterError
from database_parameters import parameter_values as database_parameter_values
from platform_parameters import ParameterError as PlatformParameterError
from platform_parameters import parameter_values as platform_parameter_values
from preflight_platform import PreflightError as PlatformPreflightError
from preflight_platform import validate_budget


class DatabaseNetworkPreflightError(RuntimeError):
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
        raise DatabaseNetworkPreflightError("a database network readiness query failed") from error
    if result.returncode != 0:
        raise DatabaseNetworkPreflightError("a database network readiness query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise DatabaseNetworkPreflightError("database network readiness returned unreadable JSON") from error


def validate_local(
    config_path: Path,
    parameters_path: Path,
    database_path: Path,
    platform_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        config = load_config(config_path)
        values = parameter_values(parameters_path)
        database = database_parameter_values(database_path)
        platform = platform_parameter_values(platform_path)
    except (DatabaseNetworkParameterError, DatabaseParameterError, PlatformParameterError) as error:
        raise DatabaseNetworkPreflightError(str(error)) from error
    shared_parameters = (
        "location",
        "environment",
        "projectName",
        "databaseNetworkResourceGroupName",
        "databaseResourceGroupName",
        "platformResourceGroupName",
        "platformVnetName",
        "privateEndpointSubnetName",
        "ownerTag",
        "additionalTags",
    )
    if any(config[name] != values[name] for name in shared_parameters):
        raise DatabaseNetworkPreflightError("database network config and private parameters differ")
    if any(
        config[name] != database[name]
        for name in (
            "environment",
            "projectName",
            "databaseResourceGroupName",
            "databaseName",
            "platformResourceGroupName",
            "ownerTag",
            "additionalTags",
        )
    ):
        raise DatabaseNetworkPreflightError("database network and database contracts differ")
    if any(
        config[name] != platform[name]
        for name in (
            "location",
            "environment",
            "projectName",
            "platformResourceGroupName",
            "ownerTag",
            "additionalTags",
        )
    ):
        raise DatabaseNetworkPreflightError("database network and platform contracts differ")
    expected_vnet = f"vnet-{config['projectName']}-platform-{config['environment']}"
    if config["platformVnetName"] != expected_vnet:
        raise DatabaseNetworkPreflightError("database network VNet name differs from the foundation contract")
    return values, database, platform


def validate_azure(
    values: dict[str, Any],
    database: dict[str, Any],
    platform: dict[str, Any],
    *,
    require_budget: bool,
) -> None:
    account = az_json(["account", "show"], timeout=20)
    if account.get("environmentName") != "AzureCloud":
        raise DatabaseNetworkPreflightError("the active Azure CLI cloud must be AzureCloud")
    expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
    if expected_subscription and account.get("id") != expected_subscription:
        raise DatabaseNetworkPreflightError("the active subscription does not match AZURE_SUBSCRIPTION_ID")
    try:
        validate_budget(account, required=require_budget, maximum_amount=database["monthlyCostCeiling"])
    except PlatformPreflightError as error:
        raise DatabaseNetworkPreflightError(str(error)) from error
    providers = az_json(["provider", "list"])
    states = {item.get("namespace"): item.get("registrationState") for item in providers}
    missing = [
        name
        for name in ("Microsoft.Network", "Microsoft.Sql", "Microsoft.Resources")
        if states.get(name) != "Registered"
    ]
    if missing:
        raise DatabaseNetworkPreflightError(f"register database network provider(s): {', '.join(missing)}")

    servers = az_json(
        ["sql", "server", "list", "--resource-group", values["databaseResourceGroupName"]],
        timeout=30,
    )
    if len(servers) != 1 or servers[0].get("name") != values["sqlServerName"]:
        raise DatabaseNetworkPreflightError("the private parameters do not identify the exact WP3 SQL server")
    if servers[0].get("publicNetworkAccess") != "Disabled":
        raise DatabaseNetworkPreflightError("SQL public access must remain disabled")

    vnet = az_json(
        [
            "network",
            "vnet",
            "show",
            "--resource-group",
            values["platformResourceGroupName"],
            "--name",
            values["platformVnetName"],
        ],
        timeout=30,
    )
    if vnet.get("location") != values["location"]:
        raise DatabaseNetworkPreflightError("private endpoint location must match the platform VNet")
    subnets = (vnet.get("subnets") or [])
    subnet = next((item for item in subnets if item.get("name") == values["privateEndpointSubnetName"]), None)
    if not isinstance(subnet, dict) or subnet.get("privateEndpointNetworkPolicies") != "Disabled":
        raise DatabaseNetworkPreflightError("the dedicated private endpoint subnet is absent or restricted")

    group_name = values["databaseNetworkResourceGroupName"]
    exists = az_json(["group", "exists", "--name", group_name], timeout=20)
    if exists is True:
        group = az_json(["group", "show", "--name", group_name], timeout=20)
        stable_group_tags = dict(group_tags(values))
        stable_group_tags.pop("expiresAt")
        stable_resource_tags = dict(resource_tags(values))
        stable_resource_tags.pop("expiresAt")
        if group.get("location") != values["location"] or not tags_match(group.get("tags"), stable_group_tags):
            raise DatabaseNetworkPreflightError("database network group ownership, expiry, or location differs")
        resources = az_json(["resource", "list", "--resource-group", group_name], timeout=30)
        state = classify(resources)
        if state == "unexpected":
            raise DatabaseNetworkPreflightError("database network group contains a foreign resource")
        for resource in resources:
            if is_service_managed_network_interface(resource):
                continue
            if not tags_match(resource.get("tags"), stable_resource_tags):
                raise DatabaseNetworkPreflightError("database network resource tags differ")
        print(f"PASS WP3 database network overlay has {state} state")
    else:
        print("PASS WP3 database network group is absent and ready for reviewed creation")
    print("PASS SQL base and eastus private endpoint subnet are ready")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--database-parameters", type=Path, required=True)
    parser.add_argument("--platform-parameters", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--require-budget", action="store_true")
    args = parser.parse_args()
    try:
        values, database, platform = validate_local(
            args.config,
            args.parameters,
            args.database_parameters,
            args.platform_parameters,
        )
        print("PASS database network, database, and platform contracts agree")
        if args.offline:
            print("PASS offline database network preflight complete; no Azure calls were made")
            return 0
        if shutil.which("az") is None:
            raise DatabaseNetworkPreflightError("Azure CLI is not installed")
        validate_azure(values, database, platform, require_budget=args.require_budget)
        print("PASS read-only WP3 database network Azure preflight complete")
        print("INFO no resources were created or changed")
        return 0
    except DatabaseNetworkPreflightError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())