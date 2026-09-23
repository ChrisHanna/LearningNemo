#!/usr/bin/env python3
"""Fail closed unless the platform group is still safe for WP2a-only removal."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from platform_contract import expected_resource_tags
from platform_contract import foundation_inventory
from platform_contract import identity_inventory
from platform_contract import infrastructure_resource_group_name
from platform_contract import platform_group_tags
from platform_contract import resource_identity
from platform_contract import runtime_inventory
from platform_contract import runtime_resource_tags
from runtime_parameters import ParameterError
from runtime_parameters import parameter_values


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


def validate_inventory(values: dict[str, Any], resources: list[dict[str, Any]]) -> int:
    allowed = expected_resource_tags(values)

    actual: set[tuple[str, str]] = set()
    runtime_count = 0
    for resource in resources:
        identity = resource_identity(resource.get("type", ""), resource.get("name", ""))
        actual.add(identity)
        if identity not in allowed:
            raise CleanupError("platform group contains a resource outside the WP1/WP2a cleanup contract")
        if not tags_match(resource.get("tags"), allowed[identity]):
            raise CleanupError("a platform resource has ownership or phase tags outside the cleanup contract")
        if identity in runtime_inventory(values):
            runtime_count += 1
    if not foundation_inventory(values) | identity_inventory(values) <= actual:
        raise CleanupError("WP1 network and persistent identities must remain complete during runtime removal")
    return runtime_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        try:
            values = parameter_values(args.parameters)
        except ParameterError as error:
            raise CleanupError(str(error)) from error
        resource_group = values["platformResourceGroupName"]
        exists = az_json(["group", "exists", "--name", resource_group], timeout=20)
        if exists is not True:
            raise CleanupError("WP1 platform resource group is absent")
        group = az_json(["group", "show", "--name", resource_group], timeout=20)
        group_tags = platform_group_tags(values)
        if not tags_match(group.get("tags"), group_tags):
            raise CleanupError("platform group ownership and trust tags do not match")
        resources = az_json(["resource", "list", "--resource-group", resource_group], timeout=30)
        count = validate_inventory(values, resources)
        environment_identity = resource_identity(
            "Microsoft.App/managedEnvironments",
            values["containerAppsEnvironmentName"],
        )
        environment_exists = environment_identity in {
            resource_identity(resource.get("type", ""), resource.get("name", ""))
            for resource in resources
        }
        managed_group_name = infrastructure_resource_group_name(values)
        managed_group_exists = az_json(["group", "exists", "--name", managed_group_name], timeout=20)
        if managed_group_exists is not environment_exists:
            raise CleanupError("Container Apps environment and managed infrastructure group state differ")
        if managed_group_exists:
            managed_group = az_json(["group", "show", "--name", managed_group_name], timeout=20)
            if not tags_match(managed_group.get("tags"), runtime_resource_tags(values)):
                raise CleanupError("managed infrastructure group ownership tags do not match")
        if environment_exists:
            environment = next(
                resource
                for resource in resources
                if resource_identity(resource.get("type", ""), resource.get("name", "")) == environment_identity
            )
            expires_at = (environment.get("tags") or {}).get("expiresAt")
            try:
                expiration = dt.datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            except ValueError as error:
                raise CleanupError("runtime environment expiresAt tag is missing or malformed") from error
            if expiration.utcoffset() != dt.timedelta(0):
                raise CleanupError("runtime environment expiresAt tag must use UTC")
            if managed_group_exists and (managed_group.get("tags") or {}).get("expiresAt") != expires_at:
                raise CleanupError("managed infrastructure group expiration differs")
        print(f"PASS platform group contains {count} removable runtime resource(s), six identities, and two WP1 resources")
        print("PASS managed infrastructure group lifecycle and ownership tags match")
        return 0
    except CleanupError as error:
        print(f"FAIL delete blocked: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())