#!/usr/bin/env python3
"""Validate artifact config and materialize an expiring private ARM parameter file."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from foundation_parameters import PARAMETER_SCHEMA
from foundation_parameters import SAFE_ALIAS_PATTERN
from foundation_parameters import SECRET_NAME_FRAGMENTS
from runtime_parameters import parse_expiration


EXPECTED_CONFIG_FIELDS = {
    "schemaVersion",
    "location",
    "environment",
    "projectName",
    "artifactResourceGroupName",
    "platformResourceGroupName",
    "databaseResourceGroupName",
    "ownerTag",
    "monthlyCostCeiling",
    "additionalTags",
}
EXPECTED_PARAMETERS = EXPECTED_CONFIG_FIELDS - {"schemaVersion"} | {"expiresAt"}
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._()\-]{1,90}$")
RESERVED_TAGS = {
    "costprofile",
    "disposable",
    "environment",
    "expiresat",
    "managedby",
    "monthlycostceiling",
    "owner",
    "platformphase",
    "project",
    "trustzone",
}


class ArtifactParameterError(RuntimeError):
    pass


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArtifactParameterError(f"unable to read {label}") from error
    if not isinstance(document, dict):
        raise ArtifactParameterError(f"{label} must contain an object")
    return document


def load_config_document(config: dict[str, Any]) -> dict[str, Any]:
    if set(config) != EXPECTED_CONFIG_FIELDS or config.get("schemaVersion") != 1:
        raise ArtifactParameterError("artifact configuration fields differ")
    string_fields = EXPECTED_CONFIG_FIELDS - {"schemaVersion", "monthlyCostCeiling", "additionalTags"}
    for name in string_fields:
        value = config.get(name)
        if not isinstance(value, str) or not value:
            raise ArtifactParameterError(f"artifact configuration field {name} must be a string")
    for name in string_fields - {"location", "ownerTag"}:
        if NAME_PATTERN.fullmatch(config[name]) is None:
            raise ArtifactParameterError(f"artifact configuration field {name} is invalid")
    if SAFE_ALIAS_PATTERN.fullmatch(config["ownerTag"]) is None:
        raise ArtifactParameterError("artifact ownerTag must be a non-sensitive alias")
    if config["monthlyCostCeiling"] != 50:
        raise ArtifactParameterError("artifact monthly cost ceiling must remain 50")
    tags = config["additionalTags"]
    if not isinstance(tags, dict):
        raise ArtifactParameterError("artifact additionalTags must be an object")
    for name, value in tags.items():
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or not name
            or not value
            or name.casefold() in RESERVED_TAGS
            or any(fragment.casefold() in name.casefold() for fragment in SECRET_NAME_FRAGMENTS)
        ):
            raise ArtifactParameterError("artifact additionalTags contains an invalid value")
    return config


def load_config(path: Path) -> dict[str, Any]:
    return load_config_document(read_json(path, "artifact configuration"))


def load_materialized(path: Path) -> dict[str, Any]:
    document = read_json(path, "materialized artifact parameters")
    parameters = document.get("parameters")
    if (
        document.get("$schema") != PARAMETER_SCHEMA
        or document.get("contentVersion") != "1.0.0.0"
        or not isinstance(parameters, dict)
        or set(parameters) != EXPECTED_PARAMETERS
        or any(not isinstance(entry, dict) or set(entry) != {"value"} for entry in parameters.values())
    ):
        raise ArtifactParameterError("materialized artifact parameter contract differs")
    values = {name: entry["value"] for name, entry in parameters.items()}
    load_config_document({"schemaVersion": 1, **{name: values[name] for name in EXPECTED_CONFIG_FIELDS - {"schemaVersion"}}})
    parse_expiration(values["expiresAt"], allow_runtime_expiry=False)
    return document


def parameter_values(path: Path) -> dict[str, Any]:
    return {name: entry["value"] for name, entry in load_materialized(path)["parameters"].items()}


def materialize(config_path: Path, output: Path, expires_at: str) -> None:
    config = load_config(config_path)
    values = {name: value for name, value in config.items() if name != "schemaVersion"}
    values["expiresAt"] = parse_expiration(expires_at, allow_runtime_expiry=False)
    document = {
        "$schema": PARAMETER_SCHEMA,
        "contentVersion": "1.0.0.0",
        "parameters": {name: {"value": value} for name, value in values.items()},
    }
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)
    load_materialized(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("config", type=Path)
    get_parser = subparsers.add_parser("get-config")
    get_parser.add_argument("config", type=Path)
    get_parser.add_argument("name", choices=sorted(EXPECTED_CONFIG_FIELDS - {"schemaVersion", "additionalTags"}))
    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("config", type=Path)
    materialize_parser.add_argument("output", type=Path)
    materialize_parser.add_argument("--expires-at", required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            load_config(args.config)
            print("PASS artifact configuration contains no generated registry or identity values")
        elif args.command == "get-config":
            print(load_config(args.config)[args.name])
        else:
            materialize(args.config, args.output, args.expires_at)
            print("PASS materialized owner-only expiring artifact parameters", file=sys.stderr)
        return 0
    except ArtifactParameterError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())