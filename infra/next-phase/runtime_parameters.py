#!/usr/bin/env python3
"""Validate and materialize runtime-only WP2a ARM parameters."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

from platform_parameters import EXPECTED_PARAMETERS as PLATFORM_PARAMETERS
from platform_parameters import ParameterError
from platform_parameters import validate_document


RUNTIME_EXPIRY = "__RUNTIME__"
EXPECTED_PARAMETERS = PLATFORM_PARAMETERS | {"expiresAt"}


def parse_expiration(value: Any, *, allow_runtime_expiry: bool) -> str:
    if value == RUNTIME_EXPIRY:
        if allow_runtime_expiry:
            return RUNTIME_EXPIRY
        raise ParameterError("expiresAt must be materialized before Azure validation")
    if not isinstance(value, str):
        raise ParameterError("expiresAt must be an ISO-8601 UTC timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ParameterError("expiresAt must be an ISO-8601 UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0) or parsed.microsecond:
        raise ParameterError("expiresAt must be a whole-second UTC timestamp")
    return parsed.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def expiration_status(value: str, *, now: dt.datetime | None = None) -> str:
    normalized = parse_expiration(value, allow_runtime_expiry=False)
    expiration = dt.datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    current = now or dt.datetime.now(dt.UTC)
    if current.tzinfo is None or current.utcoffset() != dt.timedelta(0):
        raise ParameterError("expiration comparison time must use UTC")
    return "expired" if expiration <= current else "active"


def load_document(path: Path, *, allow_runtime_expiry: bool = True) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ParameterError(f"unable to read runtime parameter file: {error}") from error
    parameters = document.get("parameters") if isinstance(document, dict) else None
    if not isinstance(parameters, dict) or set(parameters) != EXPECTED_PARAMETERS:
        raise ParameterError("runtime parameter names do not match the WP2a runtime contract")
    expires_entry = parameters.get("expiresAt")
    if not isinstance(expires_entry, dict) or set(expires_entry) != {"value"}:
        raise ParameterError("parameter expiresAt must contain only a value field")
    parse_expiration(expires_entry["value"], allow_runtime_expiry=allow_runtime_expiry)
    base_document = copy.deepcopy(document)
    del base_document["parameters"]["expiresAt"]
    validate_document(base_document)
    return document


def parameter_values(path: Path, *, allow_runtime_expiry: bool = True) -> dict[str, Any]:
    document = load_document(path, allow_runtime_expiry=allow_runtime_expiry)
    return {name: entry["value"] for name, entry in document["parameters"].items()}


def materialize(source: Path, destination: Path, expires_at: str) -> None:
    normalized = parse_expiration(expires_at, allow_runtime_expiry=False)
    document = copy.deepcopy(load_document(source))
    document["parameters"]["expiresAt"]["value"] = normalized
    destination.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(destination, 0o600)
    load_document(destination, allow_runtime_expiry=False)


def main() -> int:
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
    materialize_parser.add_argument("--expires-at", required=True)
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--expires-at", required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            load_document(args.parameter_file)
            print("PASS runtime parameter file is valid and contains no secret-like fields")
        elif args.command == "get":
            value = parameter_values(args.parameter_file)[args.name]
            if not isinstance(value, str):
                raise ParameterError(f"parameter {args.name} is not a string")
            print(value)
        elif args.command == "materialize":
            materialize(args.parameter_file, args.output, args.expires_at)
            print("PASS materialized runtime expiration in private ARM parameters", file=sys.stderr)
        else:
            print(expiration_status(args.expires_at))
        return 0
    except ParameterError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())