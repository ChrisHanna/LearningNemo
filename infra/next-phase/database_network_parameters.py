#!/usr/bin/env python3
"""Materialize expiring SQL private-network parameters from owner-only state."""

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
    "databaseNetworkResourceGroupName",
    "databaseResourceGroupName",
    "databaseName",
    "platformResourceGroupName",
    "platformVnetName",
    "privateEndpointSubnetName",
    "ownerTag",
    "additionalTags",
}
EXPECTED_STATE_FIELDS = {
    "schemaVersion",
    "resourceGroupName",
    "serverName",
    "databaseName",
    "sqlAdminIdentityName",
}
EXPECTED_PARAMETERS = {
    "location",
    "environment",
    "projectName",
    "databaseNetworkResourceGroupName",
    "databaseResourceGroupName",
    "sqlServerName",
    "platformResourceGroupName",
    "platformVnetName",
    "privateEndpointSubnetName",
    "expiresAt",
    "ownerTag",
    "additionalTags",
}
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._()\-]{1,90}$")
SERVER_NAME_PATTERN = re.compile(r"^[a-z0-9-]{1,63}$")
RESERVED_TAGS = {
    "costprofile",
    "disposable",
    "environment",
    "expiresat",
    "managedby",
    "owner",
    "platformphase",
    "project",
    "trustzone",
}


class DatabaseNetworkParameterError(RuntimeError):
    pass


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DatabaseNetworkParameterError(f"unable to read {label}") from error
    if not isinstance(value, dict):
        raise DatabaseNetworkParameterError(f"{label} must contain a JSON object")
    return value


def load_config_document(config: dict[str, Any]) -> dict[str, Any]:
    if set(config) != EXPECTED_CONFIG_FIELDS or config.get("schemaVersion") != 1:
        raise DatabaseNetworkParameterError("database network configuration fields differ")
    for name in EXPECTED_CONFIG_FIELDS - {"schemaVersion", "additionalTags"}:
        if not isinstance(config.get(name), str) or not config[name]:
            raise DatabaseNetworkParameterError(f"database network field {name} must be a string")
    for name in EXPECTED_CONFIG_FIELDS - {
        "schemaVersion",
        "location",
        "ownerTag",
        "additionalTags",
    }:
        if NAME_PATTERN.fullmatch(config[name]) is None:
            raise DatabaseNetworkParameterError(f"database network field {name} is invalid")
    if SAFE_ALIAS_PATTERN.fullmatch(config["ownerTag"]) is None:
        raise DatabaseNetworkParameterError("database network ownerTag must be a safe alias")
    tags = config["additionalTags"]
    if not isinstance(tags, dict):
        raise DatabaseNetworkParameterError("database network additionalTags must be an object")
    for name, value in tags.items():
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or not name
            or not value
            or name.casefold() in RESERVED_TAGS
            or any(fragment.casefold() in name.casefold() for fragment in SECRET_NAME_FRAGMENTS)
        ):
            raise DatabaseNetworkParameterError("database network additionalTags contains an invalid value")
    return config


def load_config(path: Path) -> dict[str, Any]:
    return load_config_document(read_json(path, "database network configuration"))


def load_private_state(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    state = read_json(path, "database private state")
    if set(state) != EXPECTED_STATE_FIELDS or state.get("schemaVersion") != 1:
        raise DatabaseNetworkParameterError("database private state fields differ")
    if (
        state.get("resourceGroupName") != config["databaseResourceGroupName"]
        or state.get("databaseName") != config["databaseName"]
    ):
        raise DatabaseNetworkParameterError("database private state belongs to another database")
    if SERVER_NAME_PATTERN.fullmatch(str(state.get("serverName", ""))) is None:
        raise DatabaseNetworkParameterError("database private state contains an invalid server name")
    return state


def load_materialized(path: Path) -> dict[str, Any]:
    document = read_json(path, "materialized database network parameters")
    parameters = document.get("parameters")
    if (
        document.get("$schema") != PARAMETER_SCHEMA
        or document.get("contentVersion") != "1.0.0.0"
        or not isinstance(parameters, dict)
        or set(parameters) != EXPECTED_PARAMETERS
        or any(not isinstance(entry, dict) or set(entry) != {"value"} for entry in parameters.values())
    ):
        raise DatabaseNetworkParameterError("materialized database network parameters differ")
    values = {name: entry["value"] for name, entry in parameters.items()}
    config = {
        "schemaVersion": 1,
        "location": values["location"],
        "environment": values["environment"],
        "projectName": values["projectName"],
        "databaseNetworkResourceGroupName": values["databaseNetworkResourceGroupName"],
        "databaseResourceGroupName": values["databaseResourceGroupName"],
        "databaseName": "validation-placeholder",
        "platformResourceGroupName": values["platformResourceGroupName"],
        "platformVnetName": values["platformVnetName"],
        "privateEndpointSubnetName": values["privateEndpointSubnetName"],
        "ownerTag": values["ownerTag"],
        "additionalTags": values["additionalTags"],
    }
    load_config_document(config)
    if SERVER_NAME_PATTERN.fullmatch(str(values["sqlServerName"])) is None:
        raise DatabaseNetworkParameterError("materialized SQL server name is invalid")
    parse_expiration(values["expiresAt"], allow_runtime_expiry=False)
    return document


def parameter_values(path: Path) -> dict[str, Any]:
    return {name: entry["value"] for name, entry in load_materialized(path)["parameters"].items()}


def materialize(config_path: Path, state_path: Path, output: Path, expires_at: str) -> None:
    config = load_config(config_path)
    state = load_private_state(state_path, config)
    normalized_expiry = parse_expiration(expires_at, allow_runtime_expiry=False)
    document = {
        "$schema": PARAMETER_SCHEMA,
        "contentVersion": "1.0.0.0",
        "parameters": {
            "location": {"value": config["location"]},
            "environment": {"value": config["environment"]},
            "projectName": {"value": config["projectName"]},
            "databaseNetworkResourceGroupName": {"value": config["databaseNetworkResourceGroupName"]},
            "databaseResourceGroupName": {"value": config["databaseResourceGroupName"]},
            "sqlServerName": {"value": state["serverName"]},
            "platformResourceGroupName": {"value": config["platformResourceGroupName"]},
            "platformVnetName": {"value": config["platformVnetName"]},
            "privateEndpointSubnetName": {"value": config["privateEndpointSubnetName"]},
            "expiresAt": {"value": normalized_expiry},
            "ownerTag": {"value": config["ownerTag"]},
            "additionalTags": {"value": config["additionalTags"]},
        },
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
    materialize_parser.add_argument("--database-state", type=Path, required=True)
    materialize_parser.add_argument("--expires-at", required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            load_config(args.config)
            print("PASS database network configuration contains no generated identifiers")
        elif args.command == "get-config":
            print(load_config(args.config)[args.name])
        else:
            materialize(args.config, args.database_state, args.output, args.expires_at)
            print("PASS materialized owner-only SQL private-network parameters", file=sys.stderr)
        return 0
    except DatabaseNetworkParameterError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())