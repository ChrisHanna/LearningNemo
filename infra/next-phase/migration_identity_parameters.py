#!/usr/bin/env python3
"""Materialize a bounded cross-region SQL migration identity overlay."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from database_parameters import DatabaseParameterError
from database_parameters import parameter_values as database_parameter_values
from foundation_parameters import PARAMETER_SCHEMA
from runtime_parameters import parse_expiration


EXPECTED_PARAMETERS = {
    "location",
    "environment",
    "projectName",
    "expiresAt",
    "ownerTag",
    "monthlyCostCeiling",
    "additionalTags",
}


class MigrationIdentityParameterError(RuntimeError):
    pass


def load(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MigrationIdentityParameterError("unable to read migration identity parameters") from error
    parameters = document.get("parameters") if isinstance(document, dict) else None
    if (
        document.get("$schema") != PARAMETER_SCHEMA
        or document.get("contentVersion") != "1.0.0.0"
        or not isinstance(parameters, dict)
        or set(parameters) != EXPECTED_PARAMETERS
        or any(not isinstance(entry, dict) or set(entry) != {"value"} for entry in parameters.values())
    ):
        raise MigrationIdentityParameterError("migration identity parameter contract differs")
    values = {name: entry["value"] for name, entry in parameters.items()}
    if not all(isinstance(values[name], str) and values[name] for name in ("location", "environment", "projectName", "ownerTag")):
        raise MigrationIdentityParameterError("migration identity string parameter differs")
    if values["monthlyCostCeiling"] != 50 or not isinstance(values["additionalTags"], dict):
        raise MigrationIdentityParameterError("migration identity cost or tag contract differs")
    parse_expiration(values["expiresAt"], allow_runtime_expiry=False)
    return document


def parameter_values(path: Path) -> dict[str, Any]:
    return {name: entry["value"] for name, entry in load(path)["parameters"].items()}


def materialize(database_parameters: Path, output: Path, expires_at: str) -> None:
    try:
        database = database_parameter_values(database_parameters)
    except DatabaseParameterError as error:
        raise MigrationIdentityParameterError(str(error)) from error
    values = {
        "location": database["location"],
        "environment": database["environment"],
        "projectName": database["projectName"],
        "expiresAt": parse_expiration(expires_at, allow_runtime_expiry=False),
        "ownerTag": database["ownerTag"],
        "monthlyCostCeiling": database["monthlyCostCeiling"],
        "additionalTags": database["additionalTags"],
    }
    document = {
        "$schema": PARAMETER_SCHEMA,
        "contentVersion": "1.0.0.0",
        "parameters": {name: {"value": value} for name, value in values.items()},
    }
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)
    load(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("--database-parameters", type=Path, required=True)
    materialize_parser.add_argument("--output", type=Path, required=True)
    materialize_parser.add_argument("--expires-at", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("parameters", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "materialize":
            materialize(args.database_parameters, args.output, args.expires_at)
            print("PASS materialized bounded SQL migration identity overlay", file=sys.stderr)
        else:
            load(args.parameters)
            print("PASS SQL migration identity overlay parameters are valid")
        return 0
    except MigrationIdentityParameterError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())