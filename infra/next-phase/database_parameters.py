#!/usr/bin/env python3
"""Validate the identifier-free WP3 Azure SQL parameter contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from foundation_parameters import PARAMETER_SCHEMA
from foundation_parameters import SAFE_ALIAS_PATTERN
from foundation_parameters import SECRET_NAME_FRAGMENTS


EXPECTED_PARAMETERS = {
    "location",
    "environment",
    "projectName",
    "platformResourceGroupName",
    "databaseResourceGroupName",
    "databaseName",
    "ownerTag",
    "monthlyCostCeiling",
    "autoPauseDelayMinutes",
    "minVcores",
    "maxVcores",
    "maxSizeBytes",
    "useFreeLimit",
    "freeLimitExhaustionBehavior",
    "additionalTags",
}
STRING_PARAMETERS = {
    "location",
    "environment",
    "projectName",
    "platformResourceGroupName",
    "databaseResourceGroupName",
    "databaseName",
    "ownerTag",
    "freeLimitExhaustionBehavior",
    "minVcores",
}
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._()\-]{1,90}$")
RESERVED_TAGS = {
    "project",
    "environment",
    "managedby",
    "owner",
    "costprofile",
    "monthlycostceiling",
    "trustzone",
    "platformphase",
    "disposable",
    "freelimit",
}


class DatabaseParameterError(RuntimeError):
    pass


def load_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DatabaseParameterError("unable to read database parameters") from error
    parameters = document.get("parameters") if isinstance(document, dict) else None
    if (
        document.get("$schema") != PARAMETER_SCHEMA
        or document.get("contentVersion") != "1.0.0.0"
        or not isinstance(parameters, dict)
        or set(parameters) != EXPECTED_PARAMETERS
    ):
        raise DatabaseParameterError("database parameter contract differs")
    if any(not isinstance(entry, dict) or set(entry) != {"value"} for entry in parameters.values()):
        raise DatabaseParameterError("each database parameter must contain only a value")
    values = {name: entry["value"] for name, entry in parameters.items()}
    for name in STRING_PARAMETERS:
        if not isinstance(values[name], str) or not values[name]:
            raise DatabaseParameterError(f"database parameter {name} must be a non-empty string")
    for name in (
        "environment",
        "projectName",
        "platformResourceGroupName",
        "databaseResourceGroupName",
        "databaseName",
    ):
        if NAME_PATTERN.fullmatch(values[name]) is None:
            raise DatabaseParameterError(f"database parameter {name} has invalid characters")
    if SAFE_ALIAS_PATTERN.fullmatch(values["ownerTag"]) is None:
        raise DatabaseParameterError("database ownerTag must be a non-sensitive alias")
    if values["monthlyCostCeiling"] != 50:
        raise DatabaseParameterError("database monthly cost ceiling must remain 50")
    if values["autoPauseDelayMinutes"] != 60:
        raise DatabaseParameterError("free database auto-pause delay must remain 60 minutes")
    if values["minVcores"] != "0.5" or values["maxVcores"] != 2:
        raise DatabaseParameterError("free database vCore bounds must remain 0.5 through 2")
    if values["maxSizeBytes"] != 34359738368:
        raise DatabaseParameterError("free database size must remain capped at 32 GiB")
    if values["useFreeLimit"] is not True or values["freeLimitExhaustionBehavior"] != "AutoPause":
        raise DatabaseParameterError("database must auto-pause when the free limit is exhausted")
    tags = values["additionalTags"]
    if not isinstance(tags, dict):
        raise DatabaseParameterError("database additionalTags must be an object")
    for name, value in tags.items():
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or not name
            or not value
            or name.casefold() in RESERVED_TAGS
            or any(fragment.casefold() in name.casefold() for fragment in SECRET_NAME_FRAGMENTS)
        ):
            raise DatabaseParameterError("database additionalTags contains an invalid value")
    return document


def parameter_values(path: Path) -> dict[str, Any]:
    return {name: entry["value"] for name, entry in load_document(path)["parameters"].items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parameters", type=Path)
    args = parser.parse_args()
    try:
        load_document(args.parameters)
        print("PASS database parameters enforce free-limit auto-pause and contain no identifiers")
        return 0
    except DatabaseParameterError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())