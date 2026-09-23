#!/usr/bin/env python3
"""Fail closed unless the WP3 data resource group contains only the base."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from database_contract import classify
from database_contract import group_tags
from database_contract import is_master_database
from database_contract import resource_tags
from database_contract import tags_match
from database_parameters import parameter_values


class CleanupError(RuntimeError):
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
        raise CleanupError("database cleanup readiness query failed")
    return json.loads(result.stdout.lstrip("\ufeff"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    args = parser.parse_args()
    try:
        values = parameter_values(args.parameters)
        group_name = values["databaseResourceGroupName"]
        if az_json(["group", "exists", "--name", group_name]) is not True:
            print("PASS WP3 database group is already absent")
            return 0
        group = az_json(["group", "show", "--name", group_name])
        if not tags_match(group.get("tags"), group_tags(values)):
            raise CleanupError("database group ownership tags differ")
        resources = az_json(["resource", "list", "--resource-group", group_name])
        state = classify(resources)
        if state not in {"partial", "complete"}:
            raise CleanupError("database group contains a foreign or network resource; remove the overlay first")
        expected_tags = resource_tags(values)
        for resource in resources:
            if is_master_database(resource):
                continue
            expected = expected_tags
            if str(resource.get("type", "")).casefold() == "microsoft.managedidentity/userassignedidentities":
                expected = {**expected_tags, "identityPurpose": "database-bootstrap-admin"}
            if not tags_match(resource.get("tags"), expected):
                raise CleanupError("database resource tags differ")
        print(f"PASS owned WP3 database {state} state is eligible for resource-group removal")
        print("PASS no private endpoint, migration compute, or foreign resource is present")
        return 0
    except (CleanupError, json.JSONDecodeError) as error:
        print(f"FAIL delete blocked: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())