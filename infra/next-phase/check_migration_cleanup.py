#!/usr/bin/env python3
"""Fail closed unless only the exact WP3 one-shot migration job can be removed."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from migration_contract import classify
from migration_contract import job_name
from migration_contract import tags
from migration_contract import tags_match
from migration_parameters import MigrationParameterError
from migration_parameters import parameter_values


class MigrationCleanupError(RuntimeError):
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
        raise MigrationCleanupError("migration cleanup readiness query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise MigrationCleanupError("migration cleanup returned unreadable JSON") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    args = parser.parse_args()
    try:
        try:
            values = parameter_values(args.parameters)
        except MigrationParameterError as error:
            raise MigrationCleanupError(str(error)) from error
        group_name = values["migrationResourceGroupName"]
        if az_json(["group", "exists", "--name", group_name]) is not True:
            print("PASS migration resource group is already absent")
            return 0
        group = az_json(["group", "show", "--name", group_name])
        if group.get("location") != values["location"] or not tags_match(group.get("tags"), tags(values)):
            raise MigrationCleanupError("migration group ownership or artifact tags differ")
        resources = az_json(["resource", "list", "--resource-group", group_name])
        state = classify(resources)
        if state not in {"absent", "complete"}:
            raise MigrationCleanupError("migration group contains a foreign resource")
        if state == "complete":
            resource = resources[0]
            if resource.get("name") != job_name(values) or not tags_match(resource.get("tags"), tags(values)):
                raise MigrationCleanupError("migration job ownership or artifact tags differ")
        print(f"PASS exact migration {state} state is eligible for removal")
        return 0
    except MigrationCleanupError as error:
        print(f"FAIL delete blocked: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())