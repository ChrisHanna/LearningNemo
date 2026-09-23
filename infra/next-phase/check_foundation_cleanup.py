#!/usr/bin/env python3
"""Fail closed unless WP1 resource groups contain only exact foundation resources."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from foundation_parameters import ParameterError
from foundation_parameters import parameter_values


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


def parse_expiration(value: Any) -> dt.date:
    if not isinstance(value, str):
        raise CleanupError("SAW expiresOn tag is missing")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise CleanupError("SAW expiresOn tag is not a valid ISO date") from error


def validate_group_state(
    values: dict[str, Any],
    kind: str,
    group: dict[str, Any],
    resources: list[dict[str, Any]],
) -> None:
    project = values["projectName"]
    environment = values["environment"]
    common_tags = {
        "project": project,
        "environment": environment,
        "managedBy": "bicep",
        "owner": values["ownerTag"],
        "costProfile": "low-cost-poc",
        **values["additionalTags"],
    }
    if kind == "platform":
        expected_group_tags = {
            **common_tags,
            "trustZone": "trusted-platform",
            "disposable": "false",
        }
        resource_tags = common_tags
        expected = {
            ("microsoft.network/networksecuritygroups", f"nsg-vnet-{project}-platform-{environment}-container-apps"),
            ("microsoft.network/virtualnetworks", f"vnet-{project}-platform-{environment}"),
        }
    elif kind == "saw":
        expires_on = str(parse_expiration((group.get("tags") or {}).get("expiresOn")))
        expected_group_tags = {
            **common_tags,
            "trustZone": "untrusted-agent-workspace",
            "disposable": "true",
            "expiresOn": expires_on,
            "sawMaturity": "network-foundation-only",
        }
        resource_tags = expected_group_tags
        expected = {
            ("microsoft.network/networksecuritygroups", f"nsg-vnet-{project}-saw-{environment}-workspace"),
            ("microsoft.network/routetables", f"rt-vnet-{project}-saw-{environment}-workspace"),
            ("microsoft.network/virtualnetworks", f"vnet-{project}-saw-{environment}"),
        }
    else:
        raise CleanupError("unknown foundation resource-group kind")

    if group.get("location") != values["location"] or not tags_match(group.get("tags"), expected_group_tags):
        raise CleanupError(f"{kind} resource-group location or ownership tags differ")
    actual = {
        (str(resource.get("type", "")).casefold(), str(resource.get("name", "")).casefold())
        for resource in resources
    }
    normalized_expected = {(resource_type.casefold(), name.casefold()) for resource_type, name in expected}
    if actual != normalized_expected:
        raise CleanupError(f"{kind} resource inventory differs from the exact WP1 cleanup contract")
    if not all(tags_match(resource.get("tags"), resource_tags) for resource in resources):
        raise CleanupError(f"{kind} resource ownership tags differ")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--include-platform", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        try:
            values = parameter_values(args.parameters)
        except ParameterError as error:
            raise CleanupError(str(error)) from error
        groups = [("saw", values["sawResourceGroupName"])]
        if args.include_platform:
            groups.append(("platform", values["platformResourceGroupName"]))
        for kind, name in groups:
            exists = az_json(["group", "exists", "--name", name], timeout=20)
            if exists is False:
                print(f"PASS {kind} foundation resource group is absent")
                continue
            if exists is not True:
                raise CleanupError(f"could not determine whether the {kind} resource group exists")
            group = az_json(["group", "show", "--name", name], timeout=20)
            resources = az_json(["resource", "list", "--resource-group", name], timeout=30)
            validate_group_state(values, kind, group, resources)
            print(f"PASS {kind} resource group matches the exact WP1 cleanup contract")
        return 0
    except CleanupError as error:
        print(f"FAIL delete blocked: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())