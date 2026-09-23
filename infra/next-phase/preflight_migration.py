#!/usr/bin/env python3
"""Read-only local and Azure preflight for the one-shot SQL migration job."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from database_network_parameters import DatabaseNetworkParameterError
from database_network_parameters import parameter_values as network_parameter_values
from migration_contract import classify
from migration_contract import tags
from migration_contract import tags_match
from migration_parameters import MigrationParameterError
from migration_parameters import load_config
from migration_parameters import parameter_values
from runtime_parameters import ParameterError
from runtime_parameters import parameter_values as runtime_parameter_values


class MigrationPreflightError(RuntimeError):
    pass


def parse_utc(value: Any) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise MigrationPreflightError("migration dependency expiration is invalid") from error
    if parsed.utcoffset() != dt.timedelta(0):
        raise MigrationPreflightError("migration dependency expiration must use UTC")
    return parsed


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
        raise MigrationPreflightError("a migration readiness query failed") from error
    if result.returncode != 0:
        raise MigrationPreflightError("a migration readiness query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise MigrationPreflightError("migration readiness returned unreadable JSON") from error


def validate_local(
    config_path: Path,
    parameters_path: Path,
    runtime_path: Path,
    network_path: Path,
    *,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        config = load_config(config_path)
        values = parameter_values(parameters_path)
        runtime = runtime_parameter_values(runtime_path, allow_runtime_expiry=False)
        network = network_parameter_values(network_path)
    except (MigrationParameterError, ParameterError, DatabaseNetworkParameterError) as error:
        raise MigrationPreflightError(str(error)) from error
    for name in (
        "location",
        "environment",
        "projectName",
        "migrationResourceGroupName",
        "platformResourceGroupName",
        "containerAppsEnvironmentName",
        "databaseResourceGroupName",
        "databaseName",
        "ownerTag",
        "additionalTags",
    ):
        if config[name] != values[name]:
            raise MigrationPreflightError("migration config and private parameters differ")
    for name in (
        "location",
        "environment",
        "projectName",
        "platformResourceGroupName",
        "containerAppsEnvironmentName",
        "ownerTag",
        "additionalTags",
    ):
        if config[name] != runtime[name]:
            raise MigrationPreflightError("migration and runtime contracts differ")
    if (
        network["databaseResourceGroupName"] != config["databaseResourceGroupName"]
        or f"{network['sqlServerName']}.database.windows.net" != values["sqlServerHostname"]
    ):
        raise MigrationPreflightError("migration and database network contracts differ")
    current = now or dt.datetime.now(dt.UTC)
    expires = parse_utc(values["expiresAt"])
    if expires <= current or expires > current + dt.timedelta(hours=24):
        raise MigrationPreflightError("migration expiration is outside the allowed lifetime")
    if expires > parse_utc(runtime["expiresAt"]) or expires > parse_utc(network["expiresAt"]):
        raise MigrationPreflightError("migration expiration exceeds a required runtime or network overlay")
    return values, runtime


def validate_azure(values: dict[str, Any]) -> None:
    account = az_json(["account", "show"], timeout=20)
    expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
    if account.get("environmentName") != "AzureCloud" or (
        expected_subscription and account.get("id") != expected_subscription
    ):
        raise MigrationPreflightError("active Azure account or cloud differs")
    environment = az_json(
        [
            "containerapp",
            "env",
            "show",
            "--resource-group",
            values["platformResourceGroupName"],
            "--name",
            values["containerAppsEnvironmentName"],
        ],
        timeout=45,
    )
    if (environment.get("properties") or {}).get("provisioningState") != "Succeeded":
        raise MigrationPreflightError("Container Apps runtime is not ready")
    registries = az_json(["acr", "list"], timeout=45)
    matching_registries = [
        registry
        for registry in registries
        if registry.get("loginServer") == values["registryServer"]
    ]
    if (
        len(matching_registries) != 1
        or matching_registries[0].get("adminUserEnabled") is not False
        or matching_registries[0].get("anonymousPullEnabled") not in (None, False)
    ):
        raise MigrationPreflightError("managed-identity artifact registry differs")
    sql_servers = az_json(
        ["sql", "server", "list", "--resource-group", values["databaseResourceGroupName"]],
        timeout=30,
    )
    if (
        len(sql_servers) != 1
        or sql_servers[0].get("fullyQualifiedDomainName") != values["sqlServerHostname"]
        or sql_servers[0].get("publicNetworkAccess") != "Disabled"
    ):
        raise MigrationPreflightError("private SQL server differs")
    group_name = values["migrationResourceGroupName"]
    if az_json(["group", "exists", "--name", group_name], timeout=20) is True:
        group = az_json(["group", "show", "--name", group_name], timeout=20)
        resources = az_json(["resource", "list", "--resource-group", group_name], timeout=30)
        stable_tags = dict(tags(values))
        for tag_name in ("expiresAt", "imageDigest", "migrationBundleHash"):
            stable_tags.pop(tag_name)
        if group.get("location") != values["location"] or not tags_match(group.get("tags"), stable_tags):
            raise MigrationPreflightError("migration group ownership, expiry, or location differs")
        if classify(resources) == "unexpected":
            raise MigrationPreflightError("migration group contains a foreign resource")
        print(f"PASS migration job has {classify(resources)} state")
    else:
        print("PASS migration resource group is absent and ready for reviewed creation")
    print("PASS private SQL and VNet-integrated Container Apps runtime are ready")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--runtime-parameters", type=Path, required=True)
    parser.add_argument("--network-parameters", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    try:
        values, _runtime = validate_local(
            args.config,
            args.parameters,
            args.runtime_parameters,
            args.network_parameters,
        )
        print("PASS migration, runtime, and database network contracts agree")
        if args.offline:
            print("PASS offline migration preflight complete; no Azure calls were made")
            return 0
        if shutil.which("az") is None:
            raise MigrationPreflightError("Azure CLI is not installed")
        validate_azure(values)
        print("PASS read-only migration Azure preflight complete")
        print("INFO no resources were created or changed")
        return 0
    except MigrationPreflightError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())