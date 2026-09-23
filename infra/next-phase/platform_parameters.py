#!/usr/bin/env python3
"""Validate the non-secret ARM parameter contract for WP2a."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from foundation_parameters import PARAMETER_SCHEMA
from foundation_parameters import RESERVED_TAG_NAMES
from foundation_parameters import SAFE_ALIAS_PATTERN
from foundation_parameters import SECRET_NAME_FRAGMENTS


STRING_PARAMETERS = {
    "location",
    "environment",
    "projectName",
    "platformResourceGroupName",
    "containerAppsEnvironmentName",
    "ownerTag",
}
OBJECT_PARAMETERS = {"additionalTags"}
INTEGER_PARAMETERS = {"monthlyCostCeiling"}
EXPECTED_PARAMETERS = STRING_PARAMETERS | OBJECT_PARAMETERS | INTEGER_PARAMETERS
RESOURCE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._()\-]{1,90}$")
SHORT_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")
CONTAINER_ENVIRONMENT_PATTERN = re.compile(r"^[a-z](?:[a-z0-9-]{0,58}[a-z0-9])?$")
PLATFORM_RESERVED_TAG_NAMES = RESERVED_TAG_NAMES | {"platformphase", "identitypurpose"}


class ParameterError(RuntimeError):
    pass


def validate_document(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("$schema") != PARAMETER_SCHEMA:
        raise ParameterError("platform parameters use an unsupported ARM schema")
    if document.get("contentVersion") != "1.0.0.0":
        raise ParameterError("platform parameter contentVersion must be 1.0.0.0")
    parameters = document.get("parameters")
    if not isinstance(parameters, dict) or set(parameters) != EXPECTED_PARAMETERS:
        raise ParameterError("platform parameter names do not match the WP2a contract")
    for name, entry in parameters.items():
        if any(fragment.casefold() in name.casefold() for fragment in SECRET_NAME_FRAGMENTS):
            raise ParameterError(f"secret-like parameter name is prohibited: {name}")
        if not isinstance(entry, dict) or set(entry) != {"value"}:
            raise ParameterError(f"parameter {name} must contain only a value field")
        value = entry["value"]
        if name in STRING_PARAMETERS and (not isinstance(value, str) or not value):
            raise ParameterError(f"parameter {name} must be a non-empty string")
        if name in OBJECT_PARAMETERS and not isinstance(value, dict):
            raise ParameterError(f"parameter {name} must be an object")
        if name in INTEGER_PARAMETERS and (isinstance(value, bool) or not isinstance(value, int)):
            raise ParameterError(f"parameter {name} must be an integer")
    monthly_ceiling = parameters["monthlyCostCeiling"]["value"]
    if not 1 <= monthly_ceiling <= 100:
        raise ParameterError("monthlyCostCeiling must be between 1 and 100 billing-currency units")
    for name in ("environment", "projectName"):
        if SHORT_NAME_PATTERN.fullmatch(parameters[name]["value"]) is None:
            raise ParameterError(f"parameter {name} must be a short lowercase resource-name segment")
    if RESOURCE_NAME_PATTERN.fullmatch(parameters["platformResourceGroupName"]["value"]) is None:
        raise ParameterError("platformResourceGroupName contains unsupported name characters")
    if CONTAINER_ENVIRONMENT_PATTERN.fullmatch(parameters["containerAppsEnvironmentName"]["value"]) is None:
        raise ParameterError("containerAppsEnvironmentName is not a valid lowercase Container Apps environment name")
    project = parameters["projectName"]["value"]
    environment = parameters["environment"]["value"]
    if any(
        len(name) > 128
        for name in (
            f"id-{project}-control-{environment}",
            f"id-{project}-diagnostic-{environment}",
            f"id-{project}-remediation-{environment}",
            f"id-{project}-verifier-{environment}",
            f"id-{project}-query-runner-{environment}",
            f"id-{project}-workspace-controller-{environment}",
        )
    ):
        raise ParameterError("derived managed identity name exceeds 128 characters")
    if len(f"mrg-{project}-container-apps-{environment}") > 90:
        raise ParameterError("derived Container Apps managed resource-group name exceeds 90 characters")
    if SAFE_ALIAS_PATTERN.fullmatch(parameters["ownerTag"]["value"]) is None:
        raise ParameterError("ownerTag must be a non-sensitive alias using safe name characters")
    additional_tags = parameters["additionalTags"]["value"]
    for key, value in additional_tags.items():
        if not isinstance(key, str) or not isinstance(value, str) or not key or not value:
            raise ParameterError("additionalTags must contain non-empty string keys and values")
        if key.casefold() in PLATFORM_RESERVED_TAG_NAMES:
            raise ParameterError(f"additionalTags cannot override managed tag {key}")
        if any(fragment.casefold() in key.casefold() for fragment in SECRET_NAME_FRAGMENTS):
            raise ParameterError(f"secret-like additional tag name is prohibited: {key}")
        if len(key) > 64 or len(value) > 128 or "\n" in value or "\r" in value:
            raise ParameterError(f"additional tag {key} is too long or contains a line break")
    return document


def load_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ParameterError(f"unable to read platform parameter file: {error}") from error
    return validate_document(document)


def parameter_values(path: Path) -> dict[str, Any]:
    document = load_document(path)
    return {name: entry["value"] for name, entry in document["parameters"].items()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("parameter_file", type=Path)
    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("parameter_file", type=Path)
    get_parser.add_argument("name", choices=sorted(EXPECTED_PARAMETERS))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "validate":
            load_document(args.parameter_file)
            print("PASS platform parameter file is valid and contains no secret-like fields")
        else:
            value = parameter_values(args.parameter_file)[args.name]
            if not isinstance(value, str):
                raise ParameterError(f"parameter {args.name} is not a string")
            print(value)
        return 0
    except ParameterError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())