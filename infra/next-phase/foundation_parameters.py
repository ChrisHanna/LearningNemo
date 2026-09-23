#!/usr/bin/env python3
"""Validate and materialize non-secret ARM parameters for the WP1 foundation."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any


PARAMETER_SCHEMA = "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#"
RUNTIME_EXPIRY = "__RUNTIME__"
SAFE_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9._()\-]{1,64}$")
STRING_PARAMETERS = {
    "location",
    "environment",
    "projectName",
    "platformResourceGroupName",
    "sawResourceGroupName",
    "platformVnetAddressPrefix",
    "containerAppsSubnetPrefix",
    "privateEndpointSubnetPrefix",
    "sawVnetAddressPrefix",
    "sawWorkspaceSubnetPrefix",
    "sawFirewallSubnetPrefix",
    "ownerTag",
    "expiresOn",
}
OBJECT_PARAMETERS = {"additionalTags"}
EXPECTED_PARAMETERS = STRING_PARAMETERS | OBJECT_PARAMETERS
SECRET_NAME_FRAGMENTS = ("secret", "password", "token", "credential", "apiKey", "privateKey")
RESERVED_TAG_NAMES = {
    "project",
    "environment",
    "managedby",
    "owner",
    "costprofile",
    "trustzone",
    "disposable",
    "expireson",
    "sawmaturity",
}


class ParameterError(RuntimeError):
    pass


def load_document(path: Path, *, allow_runtime_expiry: bool = True) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ParameterError(f"unable to read parameter file: {error}") from error
    if not isinstance(document, dict):
        raise ParameterError("parameter file must contain a JSON object")
    if document.get("$schema") != PARAMETER_SCHEMA:
        raise ParameterError("parameter file uses an unsupported ARM parameter schema")
    if document.get("contentVersion") != "1.0.0.0":
        raise ParameterError("parameter file contentVersion must be 1.0.0.0")
    raw_parameters = document.get("parameters")
    if not isinstance(raw_parameters, dict):
        raise ParameterError("parameter file must contain a parameters object")
    parameter_names = set(raw_parameters)
    missing = sorted(EXPECTED_PARAMETERS - parameter_names)
    unexpected = sorted(parameter_names - EXPECTED_PARAMETERS)
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        raise ParameterError(f"parameter names do not match the WP1 contract ({'; '.join(details)})")
    for name in parameter_names:
        normalized = name.casefold()
        if any(fragment.casefold() in normalized for fragment in SECRET_NAME_FRAGMENTS):
            raise ParameterError(f"secret-like parameter name is prohibited: {name}")
        entry = raw_parameters[name]
        if not isinstance(entry, dict) or set(entry) != {"value"}:
            raise ParameterError(f"parameter {name} must contain only a value field")
        value = entry["value"]
        if name in STRING_PARAMETERS and (not isinstance(value, str) or not value):
            raise ParameterError(f"parameter {name} must be a non-empty string")
        if name in OBJECT_PARAMETERS and not isinstance(value, dict):
            raise ParameterError(f"parameter {name} must be an object")
    expires_on = raw_parameters["expiresOn"]["value"]
    owner_tag = raw_parameters["ownerTag"]["value"]
    if SAFE_ALIAS_PATTERN.fullmatch(owner_tag) is None:
        raise ParameterError("ownerTag must be a non-sensitive alias using safe name characters")
    additional_tags = raw_parameters["additionalTags"]["value"]
    for key, value in additional_tags.items():
        if not isinstance(key, str) or not isinstance(value, str) or not key or not value:
            raise ParameterError("additionalTags must contain non-empty string keys and values")
        if key.casefold() in RESERVED_TAG_NAMES:
            raise ParameterError(f"additionalTags cannot override managed tag {key}")
        if any(fragment.casefold() in key.casefold() for fragment in SECRET_NAME_FRAGMENTS):
            raise ParameterError(f"secret-like additional tag name is prohibited: {key}")
        if len(key) > 64 or len(value) > 128 or "\n" in value or "\r" in value:
            raise ParameterError(f"additional tag {key} is too long or contains a line break")
    if expires_on == RUNTIME_EXPIRY:
        if not allow_runtime_expiry:
            raise ParameterError("expiresOn must be materialized before Azure validation")
    else:
        try:
            dt.date.fromisoformat(expires_on)
        except ValueError as error:
            raise ParameterError("expiresOn must use YYYY-MM-DD format") from error
    return document


def parameter_values(path: Path, *, allow_runtime_expiry: bool = True) -> dict[str, Any]:
    document = load_document(path, allow_runtime_expiry=allow_runtime_expiry)
    return {name: entry["value"] for name, entry in document["parameters"].items()}


def materialize(source: Path, destination: Path, expires_on: str) -> None:
    try:
        dt.date.fromisoformat(expires_on)
    except ValueError as error:
        raise ParameterError("expiresOn must use YYYY-MM-DD format") from error
    document = copy.deepcopy(load_document(source))
    document["parameters"]["expiresOn"]["value"] = expires_on
    destination.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    load_document(destination, allow_runtime_expiry=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("parameter_file", type=Path)
    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("parameter_file", type=Path)
    get_parser.add_argument("name", choices=sorted(EXPECTED_PARAMETERS))
    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("parameter_file", type=Path)
    materialize_parser.add_argument("output", type=Path)
    materialize_parser.add_argument("--expires-on", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "validate":
            load_document(args.parameter_file)
            print("PASS foundation parameter file is valid and contains no secret-like fields")
        elif args.command == "get":
            value = parameter_values(args.parameter_file)[args.name]
            if not isinstance(value, str):
                raise ParameterError(f"parameter {args.name} is not a string")
            print(value)
        elif args.command == "materialize":
            materialize(args.parameter_file, args.output, args.expires_on)
            print("PASS materialized runtime expiration in temporary ARM parameters", file=sys.stderr)
        return 0
    except ParameterError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())