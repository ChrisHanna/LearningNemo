#!/usr/bin/env python3
"""Verify the persistent WP2a identities without printing Azure identity IDs."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from platform_contract import IDENTITY_PURPOSES
from platform_contract import foundation_inventory
from platform_contract import identity_inventory
from platform_contract import identity_resource_tags
from platform_contract import platform_group_tags
from platform_contract import platform_inventory
from platform_contract import resource_identity
from platform_parameters import ParameterError
from platform_parameters import parameter_values


class VerificationError(RuntimeError):
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
        raise VerificationError("Azure identity query failed or timed out") from error
    if result.returncode != 0:
        raise VerificationError("Azure identity query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise VerificationError("Azure identity query returned unreadable JSON") from error


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def tags_match(actual: Any, expected: dict[str, str]) -> bool:
    return isinstance(actual, dict) and all(actual.get(key) == value for key, value in expected.items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    args = parser.parse_args()
    try:
        try:
            values = parameter_values(args.parameters)
        except ParameterError as error:
            raise VerificationError(str(error)) from error
        account = az_json(["account", "show"], timeout=20)
        expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
        if expected_subscription:
            require(account.get("id") == expected_subscription, "active subscription mismatch")
        resource_group = values["platformResourceGroupName"]
        group = az_json(["group", "show", "--name", resource_group], timeout=20)
        require(group.get("location") == values["location"], "platform resource group location differs")
        require(tags_match(group.get("tags"), platform_group_tags(values)), "platform resource group tags differ")
        resources = az_json(["resource", "list", "--resource-group", resource_group], timeout=30)
        actual = {
            resource_identity(resource.get("type", ""), resource.get("name", ""))
            for resource in resources
        }
        require(foundation_inventory(values) | identity_inventory(values) <= actual, "identity slice is incomplete")
        require(actual <= platform_inventory(values), "platform group contains an unexpected resource")

        project = values["projectName"]
        environment = values["environment"]
        common_tags = identity_resource_tags(values)
        for suffix, purpose in IDENTITY_PURPOSES.items():
            identity = az_json(
                [
                    "resource",
                    "show",
                    "--resource-group",
                    resource_group,
                    "--name",
                    f"id-{project}-{suffix}-{environment}",
                    "--resource-type",
                    "Microsoft.ManagedIdentity/userAssignedIdentities",
                    "--api-version",
                    "2024-11-30",
                ]
            )
            require(identity.get("location") == values["location"], f"{suffix} identity location differs")
            require(
                tags_match(identity.get("tags"), {**common_tags, "identityPurpose": purpose}),
                f"{suffix} identity tags differ",
            )
            require((identity.get("properties") or {}).get("isolationScope") == "Regional", f"{suffix} identity isolation differs")
        print("PASS six persistent regional service identities and purpose tags match")
        print("PASS identity verification exposed no client or principal identifiers")
        return 0
    except VerificationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())