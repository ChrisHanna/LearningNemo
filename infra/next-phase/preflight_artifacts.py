#!/usr/bin/env python3
"""Read-only readiness checks for the expiring WP3 artifact registry."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from artifact_contract import classify
from artifact_contract import tags
from artifact_contract import tags_match
from artifact_parameters import ArtifactParameterError
from artifact_parameters import load_config
from artifact_parameters import parameter_values
from database_parameters import DatabaseParameterError
from database_parameters import parameter_values as database_parameter_values
from platform_parameters import ParameterError as PlatformParameterError
from platform_parameters import parameter_values as platform_parameter_values
from preflight_platform import PreflightError as PlatformPreflightError
from preflight_platform import validate_budget


class ArtifactPreflightError(RuntimeError):
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
        raise ArtifactPreflightError("an artifact readiness query failed") from error
    if result.returncode != 0:
        raise ArtifactPreflightError("an artifact readiness query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise ArtifactPreflightError("artifact readiness returned unreadable JSON") from error


def validate_local(
    config_path: Path,
    parameters_path: Path,
    platform_path: Path,
    database_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        config = load_config(config_path)
        values = parameter_values(parameters_path)
        platform = platform_parameter_values(platform_path)
        database = database_parameter_values(database_path)
    except (ArtifactParameterError, PlatformParameterError, DatabaseParameterError) as error:
        raise ArtifactPreflightError(str(error)) from error
    shared = (
        "location",
        "environment",
        "projectName",
        "artifactResourceGroupName",
        "platformResourceGroupName",
        "databaseResourceGroupName",
        "ownerTag",
        "monthlyCostCeiling",
        "additionalTags",
    )
    if any(config[name] != values[name] for name in shared):
        raise ArtifactPreflightError("artifact config and private parameters differ")
    for reference in (platform, database):
        for name in ("environment", "projectName", "ownerTag", "monthlyCostCeiling", "additionalTags"):
            if config[name] != reference[name]:
                raise ArtifactPreflightError("artifact and platform/database contracts differ")
    if config["location"] != platform["location"]:
        raise ArtifactPreflightError("artifact registry must share the trusted platform region")
    if config["platformResourceGroupName"] != platform["platformResourceGroupName"]:
        raise ArtifactPreflightError("artifact platform resource group differs")
    if config["databaseResourceGroupName"] != database["databaseResourceGroupName"]:
        raise ArtifactPreflightError("artifact database resource group differs")
    return values, platform


def validate_azure(values: dict[str, Any], platform: dict[str, Any], *, require_budget: bool) -> None:
    account = az_json(["account", "show"], timeout=20)
    if account.get("environmentName") != "AzureCloud":
        raise ArtifactPreflightError("the active Azure CLI cloud must be AzureCloud")
    expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
    if expected_subscription and account.get("id") != expected_subscription:
        raise ArtifactPreflightError("the active subscription does not match AZURE_SUBSCRIPTION_ID")
    try:
        validate_budget(account, required=require_budget, maximum_amount=values["monthlyCostCeiling"])
    except PlatformPreflightError as error:
        raise ArtifactPreflightError(str(error)) from error
    providers = az_json(["provider", "list"])
    states = {item.get("namespace"): item.get("registrationState") for item in providers}
    missing = [
        name
        for name in (
            "Microsoft.Authorization",
            "Microsoft.ContainerRegistry",
            "Microsoft.ManagedIdentity",
            "Microsoft.Resources",
        )
        if states.get(name) != "Registered"
    ]
    if missing:
        raise ArtifactPreflightError(f"register artifact provider(s): {', '.join(missing)}")
    required_identities = (
        (values["platformResourceGroupName"], "diagnostic"),
        (values["platformResourceGroupName"], "query-runner"),
        (values["platformResourceGroupName"], "remediation"),
        (values["platformResourceGroupName"], "verifier"),
        (values["databaseResourceGroupName"], "sql-admin"),
    )
    for resource_group, suffix in required_identities:
        identity = az_json(
            [
                "identity",
                "show",
                "--resource-group",
                resource_group,
                "--name",
                f"id-{values['projectName']}-{suffix}-{values['environment']}",
            ],
            timeout=30,
        )
        if not identity.get("principalId"):
            raise ArtifactPreflightError("a required artifact-pull identity is absent")
    group_name = values["artifactResourceGroupName"]
    if az_json(["group", "exists", "--name", group_name], timeout=20) is True:
        group = az_json(["group", "show", "--name", group_name], timeout=20)
        stable_tags = dict(tags(values))
        stable_tags.pop("expiresAt")
        if group.get("location") != values["location"] or not tags_match(group.get("tags"), stable_tags):
            raise ArtifactPreflightError("artifact resource group ownership, expiry, or location differs")
        resources = az_json(["resource", "list", "--resource-group", group_name], timeout=30)
        if classify(resources) == "unexpected":
            raise ArtifactPreflightError("artifact resource group contains a foreign resource")
        if resources and not tags_match(resources[0].get("tags"), stable_tags):
            raise ArtifactPreflightError("artifact registry tags differ")
        print(f"PASS artifact registry has {classify(resources)} state")
    else:
        print("PASS artifact resource group is absent and ready for reviewed creation")
    print("PASS providers, budget, and five pull identities are ready")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--platform-parameters", type=Path, required=True)
    parser.add_argument("--database-parameters", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--require-budget", action="store_true")
    args = parser.parse_args()
    try:
        values, platform = validate_local(
            args.config,
            args.parameters,
            args.platform_parameters,
            args.database_parameters,
        )
        print("PASS artifact, platform, and database parameter contracts agree")
        if args.offline:
            print("PASS offline artifact preflight complete; no Azure calls were made")
            return 0
        if shutil.which("az") is None:
            raise ArtifactPreflightError("Azure CLI is not installed")
        validate_azure(values, platform, require_budget=args.require_budget)
        print("PASS read-only artifact Azure preflight complete")
        print("INFO no resources were created or changed")
        return 0
    except ArtifactPreflightError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())