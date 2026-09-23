#!/usr/bin/env python3
"""Fail closed unless the WP3 SQL network overlay is exactly owned."""

from __future__ import annotations

import argparse
import json
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


class DatabaseNetworkCleanupError(RuntimeError):
    pass


def az_json(arguments: list[str]) -> Any:
    result = subprocess.run(
        ["az", *arguments, "--output", "json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode != 0:
        raise DatabaseNetworkCleanupError("database network cleanup readiness query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise DatabaseNetworkCleanupError("database network cleanup returned unreadable JSON") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--parameters", type=Path)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        group_name = config["databaseNetworkResourceGroupName"]
        if az_json(["group", "exists", "--name", group_name]) is not True:
            print("PASS WP3 database network group is already absent")
            return 0
        if args.parameters is None:
            raise DatabaseNetworkCleanupError("owner-only network parameters are required for deletion")
        values = parameter_values(args.parameters)
        comparable = (
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
        if any(config[name] != values[name] for name in comparable):
            raise DatabaseNetworkCleanupError("network config and owner-only parameters differ")
        group = az_json(["group", "show", "--name", group_name])
        if group.get("location") != values["location"] or not tags_match(group.get("tags"), group_tags(values)):
            raise DatabaseNetworkCleanupError("database network group ownership, expiry, or location differs")
        resources = az_json(["resource", "list", "--resource-group", group_name])
        state = classify(resources)
        if state not in {"partial", "complete"}:
            raise DatabaseNetworkCleanupError("database network group contains a foreign resource")
        for resource in resources:
            if is_service_managed_network_interface(resource):
                continue
            if not tags_match(resource.get("tags"), resource_tags(values)):
                raise DatabaseNetworkCleanupError("database network resource tags differ")
        print(f"PASS owned WP3 database network {state} state is eligible for removal")
        return 0
    except (DatabaseNetworkCleanupError, DatabaseNetworkParameterError) as error:
        print(f"FAIL delete blocked: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())