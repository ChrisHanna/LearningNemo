#!/usr/bin/env python3
"""Fail closed unless only owned WP2b worker resources are eligible for removal."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from platform_contract import resource_identity
from workload_contract import SERVICE_KEYS
from workload_contract import app_name
from workload_contract import combined_values
from workload_contract import identity_name
from workload_contract import workload_auth_inventory
from workload_contract import workload_inventory
from workload_contract import workload_resource_tags
from workload_parameters import WorkloadParameterError
from workload_parameters import load_config
from workload_parameters import parameter_values


APP_API_VERSION = "2025-01-01"


class CleanupError(RuntimeError):
    pass


def az_json(arguments: list[str], *, timeout: int = 45) -> Any:
    try:
        result = subprocess.run(
            ["az", *arguments, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CleanupError("Azure cleanup readiness query failed or timed out") from error
    if result.returncode != 0:
        raise CleanupError("Azure cleanup readiness query failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise CleanupError("Azure cleanup readiness query returned unreadable JSON") from error


def tags_match(actual: Any, expected: dict[str, str]) -> bool:
    return isinstance(actual, dict) and all(actual.get(key) == value for key, value in expected.items())


def resource_show(resource_group: str, name: str) -> dict[str, Any]:
    resource = az_json(
        [
            "resource",
            "show",
            "--resource-group",
            resource_group,
            "--name",
            name,
            "--resource-type",
            "Microsoft.App/containerApps",
            "--api-version",
            APP_API_VERSION,
        ]
    )
    if not isinstance(resource, dict):
        raise CleanupError("Azure worker query returned an unexpected shape")
    return resource


def validate_cleanup_inventory(
    config: dict[str, Any],
    resources: list[dict[str, Any]],
    values: dict[str, Any] | None,
) -> set[str]:
    expected_apps = workload_inventory(config)
    expected_auth = workload_auth_inventory(config)
    workload_resources = [
        resource
        for resource in resources
        if str(resource.get("type", "")).casefold()
        in {
            "microsoft.app/containerapps",
            "microsoft.app/containerapps/authconfigs",
        }
    ]
    actual = {
        resource_identity(resource.get("type", ""), resource.get("name", ""))
        for resource in workload_resources
    }
    if not actual <= expected_apps | expected_auth:
        raise CleanupError("platform group contains a workload outside the WP2b cleanup contract")
    present_apps = actual & expected_apps
    if not present_apps:
        if actual:
            raise CleanupError("an auth child exists without its expected worker app")
        return set()
    if values is None:
        raise CleanupError("private deployed workload parameters are required for removal")
    modes: set[str] = set()
    for resource in workload_resources:
        identity = resource_identity(resource.get("type", ""), resource.get("name", ""))
        if identity not in present_apps:
            continue
        mode = next(mode for mode in SERVICE_KEYS if identity[1] == app_name(config, mode).casefold())
        modes.add(mode)
        if not tags_match(resource.get("tags"), workload_resource_tags(values, mode)):
            raise CleanupError("worker ownership, expiration, mode, or image tags differ")
        app_identity = resource.get("identity") or {}
        identities = app_identity.get("userAssignedIdentities") or {}
        expected_suffix = f"/userAssignedIdentities/{identity_name(values, mode)}".casefold()
        if (
            app_identity.get("type") != "UserAssigned"
            or len(identities) != 1
            or not next(iter(identities), "").casefold().endswith(expected_suffix)
        ):
            raise CleanupError("worker managed identity differs from the cleanup contract")
        environment_id = ((resource.get("properties") or {}).get("environmentId"))
        expected_environment_suffix = (
            f"/managedEnvironments/{values['containerAppsEnvironmentName']}".casefold()
        )
        if not str(environment_id).casefold().endswith(expected_environment_suffix):
            raise CleanupError("worker environment differs from the cleanup contract")
    return modes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--parameters", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.config)
        values = None
        if args.parameters is not None:
            values = combined_values(config, parameter_values(args.parameters))
        account = az_json(["account", "show"], timeout=20)
        expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
        if expected_subscription and account.get("id") != expected_subscription:
            raise CleanupError("active subscription mismatch")
        resources = az_json(
            ["resource", "list", "--resource-group", config["platformResourceGroupName"]],
            timeout=30,
        )
        expected_apps = workload_inventory(config)
        resources = [
            resource_show(config["platformResourceGroupName"], str(resource.get("name", "")))
            if resource_identity(resource.get("type", ""), resource.get("name", ""))
            in expected_apps
            else resource
            for resource in resources
        ]
        modes = validate_cleanup_inventory(config, resources, values)
        print(f"PASS {len(modes)} owned trusted worker app(s) are eligible for exact removal")
        print("PASS no foreign Container App is included in the WP2b cleanup set")
        return 0
    except (CleanupError, WorkloadParameterError, ValueError) as error:
        print(f"FAIL delete blocked: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())