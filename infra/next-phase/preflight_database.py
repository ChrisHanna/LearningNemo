#!/usr/bin/env python3
"""Read-only Azure readiness checks for the WP3 database base."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from database_contract import classify
from database_contract import group_tags
from database_contract import is_master_database
from database_contract import resource_tags
from database_contract import tags_match
from database_parameters import DatabaseParameterError
from database_parameters import parameter_values
from platform_contract import foundation_inventory
from platform_contract import identity_inventory
from platform_contract import platform_inventory
from platform_contract import resource_identity
from platform_parameters import ParameterError as PlatformParameterError
from platform_parameters import parameter_values as platform_parameter_values
from preflight_platform import PreflightError as PlatformPreflightError
from preflight_platform import validate_budget


class DatabasePreflightError(RuntimeError):
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
        raise DatabasePreflightError("an Azure database readiness query failed or timed out") from error
    if result.returncode != 0:
        raise DatabasePreflightError("an Azure database readiness query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise DatabasePreflightError("Azure database readiness returned unreadable JSON") from error


def validate_local(database_path: Path, platform_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        database = parameter_values(database_path)
        platform = platform_parameter_values(platform_path)
    except (DatabaseParameterError, PlatformParameterError) as error:
        raise DatabasePreflightError(str(error)) from error
    shared = (
        "environment",
        "projectName",
        "platformResourceGroupName",
        "ownerTag",
        "monthlyCostCeiling",
        "additionalTags",
    )
    if any(database[name] != platform[name] for name in shared):
        raise DatabasePreflightError("database and trusted-platform parameter contracts differ")
    return database, platform


def validate_azure(
    database: dict[str, Any],
    platform: dict[str, Any],
    *,
    require_budget: bool,
) -> None:
    account = az_json(["account", "show"], timeout=20)
    if account.get("environmentName") != "AzureCloud":
        raise DatabasePreflightError("the active Azure CLI cloud must be AzureCloud")
    expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
    if expected_subscription and account.get("id") != expected_subscription:
        raise DatabasePreflightError("the active subscription does not match AZURE_SUBSCRIPTION_ID")
    try:
        validate_budget(
            account,
            required=require_budget,
            maximum_amount=database["monthlyCostCeiling"],
        )
    except PlatformPreflightError as error:
        raise DatabasePreflightError(str(error)) from error

    providers = az_json(["provider", "list"], timeout=60)
    states = {item.get("namespace"): item.get("registrationState") for item in providers}
    missing = [
        name
        for name in ("Microsoft.Sql", "Microsoft.ManagedIdentity", "Microsoft.Resources")
        if states.get(name) != "Registered"
    ]
    if missing:
        raise DatabasePreflightError(f"register database provider(s): {', '.join(missing)}")

    editions = az_json(["sql", "db", "list-editions", "--location", database["location"]], timeout=60)
    general_purpose = next(
        (item for item in editions if item.get("name") == "GeneralPurpose"),
        None,
    )
    objectives = (general_purpose or {}).get("supportedServiceLevelObjectives") or []
    objective = next((item for item in objectives if item.get("name") == "GP_S_Gen5_2"), None)
    if not isinstance(objective, dict) or objective.get("status") not in {"Available", "Default"}:
        raise DatabasePreflightError("GP_S_Gen5_2 is unavailable in the selected database region")
    free_behaviors = objective.get("supportedFreeLimitExhaustionBehaviors") or []
    if not any(
        item.get("exhaustionBehaviorType") == "AutoPause"
        and item.get("status") in {"Available", "Default"}
        for item in free_behaviors
        if isinstance(item, dict)
    ):
        raise DatabasePreflightError("the selected database region lacks free-limit auto-pause")
    min_capacities = objective.get("supportedMinCapacities") or []
    if not any(
        item.get("value") == 0.5 and item.get("status") in {"Available", "Default"}
        for item in min_capacities
        if isinstance(item, dict)
    ):
        raise DatabasePreflightError("the selected database region lacks the 0.5 vCore minimum")

    platform_group = database["platformResourceGroupName"]
    if az_json(["group", "exists", "--name", platform_group], timeout=20) is not True:
        raise DatabasePreflightError("trusted platform resource group is absent")
    platform_resources = az_json(["resource", "list", "--resource-group", platform_group], timeout=30)
    actual_platform = {
        resource_identity(item.get("type", ""), item.get("name", ""))
        for item in platform_resources
    }
    required_platform = foundation_inventory(platform) | identity_inventory(platform)
    if not required_platform <= actual_platform or not actual_platform <= platform_inventory(platform):
        raise DatabasePreflightError("WP1 network and six managed identities must match before WP3")

    database_group = database["databaseResourceGroupName"]
    group_exists = az_json(["group", "exists", "--name", database_group], timeout=20)
    if group_exists is True:
        group = az_json(["group", "show", "--name", database_group], timeout=20)
        if not tags_match(group.get("tags"), group_tags(database)):
            raise DatabasePreflightError("database resource group ownership or location differs")
        resources = az_json(["resource", "list", "--resource-group", database_group], timeout=30)
        state = classify(resources)
        if state == "unexpected":
            raise DatabasePreflightError("database group contains a resource outside the WP3 base contract")
        for resource in resources:
            if is_master_database(resource):
                continue
            expected = resource_tags(database)
            if str(resource.get("type", "")).casefold() == "microsoft.managedidentity/userassignedidentities":
                expected = {**expected, "identityPurpose": "database-bootstrap-admin"}
            if not tags_match(resource.get("tags"), expected):
                raise DatabasePreflightError("database resource ownership or cost tags differ")
        print(f"PASS WP3 database base has {state} state")
    else:
        print("PASS WP3 database resource group is absent and ready for reviewed creation")

    databases = az_json(["resource", "list", "--resource-type", "Microsoft.Sql/servers/databases"], timeout=45)
    free_offer_database_count = sum(
        str(item.get("name", "")).rsplit("/", 1)[-1].casefold() != "master"
        for item in databases
    )
    if group_exists is not True and free_offer_database_count >= 10:
        raise DatabasePreflightError("subscription already has ten SQL databases; free offer eligibility is unavailable")
    print("PASS WP1 dependencies, providers, budget, and free-database count are ready")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--platform-parameters", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--require-budget", action="store_true")
    args = parser.parse_args()
    try:
        database, platform = validate_local(args.parameters, args.platform_parameters)
        print("PASS WP3 database parameters match the trusted-platform contract")
        if args.offline:
            print("PASS offline WP3 database preflight complete; no Azure calls were made")
            return 0
        if shutil.which("az") is None:
            raise DatabasePreflightError("Azure CLI is not installed")
        validate_azure(database, platform, require_budget=args.require_budget)
        print("PASS read-only WP3 database Azure preflight complete")
        print("INFO no resources were created or changed")
        return 0
    except DatabasePreflightError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())