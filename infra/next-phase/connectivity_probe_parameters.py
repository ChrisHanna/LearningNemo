#!/usr/bin/env python3
"""Validate probe config and materialize owner-only deployment parameters."""

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
from workload_parameters import WorkloadParameterError
from workload_parameters import validate_image_reference


EXPECTED_CONFIG_FIELDS = {
    "schemaVersion",
    "location",
    "environment",
    "projectName",
    "probeResourceGroupName",
    "platformResourceGroupName",
    "containerAppsEnvironmentName",
    "artifactResourceGroupName",
    "databaseResourceGroupName",
    "ownerTag",
    "additionalTags",
}
EXPECTED_PARAMETERS = EXPECTED_CONFIG_FIELDS - {"schemaVersion"} | {
    "imageReference",
    "registryServer",
    "sqlServerHostname",
    "expiresAt",
}
DATABASE_STATE_FIELDS = {
    "schemaVersion",
    "resourceGroupName",
    "serverName",
    "databaseName",
    "sqlAdminIdentityName",
}
ARTIFACT_STATE_FIELDS = {
    "schemaVersion",
    "resourceGroupName",
    "registryName",
    "loginServer",
    "expiresAt",
}
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._()\-]{1,90}$")
REGISTRY_SERVER_PATTERN = re.compile(r"^[a-z0-9]{5,50}\.azurecr\.io$")
SQL_SERVER_PATTERN = re.compile(r"^[a-z0-9-]{1,63}$")
SQL_HOST_PATTERN = re.compile(r"^[a-z0-9-]{1,63}\.database\.windows\.net$")
RESERVED_TAGS = {
    "costprofile",
    "disposable",
    "environment",
    "expiresat",
    "imagedigest",
    "identitypurpose",
    "managedby",
    "owner",
    "platformphase",
    "project",
    "trustzone",
}


class ConnectivityProbeParameterError(RuntimeError):
    pass


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConnectivityProbeParameterError(f"unable to read {label}") from error
    if not isinstance(value, dict):
        raise ConnectivityProbeParameterError(f"{label} must contain an object")
    return value


def load_config_document(config: dict[str, Any]) -> dict[str, Any]:
    if set(config) != EXPECTED_CONFIG_FIELDS or config.get("schemaVersion") != 1:
        raise ConnectivityProbeParameterError("connectivity probe configuration fields differ")
    for name in EXPECTED_CONFIG_FIELDS - {"schemaVersion", "additionalTags"}:
        value = config.get(name)
        if not isinstance(value, str) or not value:
            raise ConnectivityProbeParameterError(f"connectivity probe field {name} must be a string")
    for name in EXPECTED_CONFIG_FIELDS - {"schemaVersion", "location", "ownerTag", "additionalTags"}:
        if NAME_PATTERN.fullmatch(config[name]) is None:
            raise ConnectivityProbeParameterError(f"connectivity probe field {name} is invalid")
    if SAFE_ALIAS_PATTERN.fullmatch(config["ownerTag"]) is None:
        raise ConnectivityProbeParameterError("connectivity probe ownerTag is invalid")
    tags = config.get("additionalTags")
    if not isinstance(tags, dict):
        raise ConnectivityProbeParameterError("connectivity probe additionalTags must be an object")
    for name, value in tags.items():
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or not name
            or not value
            or name.casefold() in RESERVED_TAGS
            or any(fragment.casefold() in name.casefold() for fragment in SECRET_NAME_FRAGMENTS)
        ):
            raise ConnectivityProbeParameterError("connectivity probe additionalTags are invalid")
    return config


def load_config(path: Path) -> dict[str, Any]:
    return load_config_document(read_json(path, "connectivity probe configuration"))


def validate_materialized_values(values: dict[str, Any]) -> None:
    load_config_document(
        {
            "schemaVersion": 1,
            **{name: values[name] for name in EXPECTED_CONFIG_FIELDS - {"schemaVersion"}},
        }
    )
    try:
        image_reference = validate_image_reference(values["imageReference"])
    except WorkloadParameterError as error:
        raise ConnectivityProbeParameterError(str(error)) from error
    if (
        REGISTRY_SERVER_PATTERN.fullmatch(str(values["registryServer"])) is None
        or not image_reference.startswith(f"{values['registryServer']}/")
    ):
        raise ConnectivityProbeParameterError("connectivity probe registry binding differs")
    if SQL_HOST_PATTERN.fullmatch(str(values["sqlServerHostname"])) is None:
        raise ConnectivityProbeParameterError("connectivity probe SQL hostname is invalid")
    parse_expiration(values["expiresAt"], allow_runtime_expiry=False)


def parameter_values(path: Path) -> dict[str, Any]:
    document = read_json(path, "connectivity probe parameters")
    parameters = document.get("parameters")
    if (
        document.get("$schema") != PARAMETER_SCHEMA
        or document.get("contentVersion") != "1.0.0.0"
        or not isinstance(parameters, dict)
        or set(parameters) != EXPECTED_PARAMETERS
        or any(not isinstance(entry, dict) or set(entry) != {"value"} for entry in parameters.values())
    ):
        raise ConnectivityProbeParameterError("connectivity probe parameter contract differs")
    values = {name: entry["value"] for name, entry in parameters.items()}
    validate_materialized_values(values)
    return values


def materialize(
    config_path: Path,
    database_state_path: Path,
    artifact_state_path: Path,
    image_reference_path: Path,
    output: Path,
    expires_at: str,
) -> None:
    config = load_config(config_path)
    database_state = read_json(database_state_path, "database private state")
    artifact_state = read_json(artifact_state_path, "artifact private state")
    if set(database_state) != DATABASE_STATE_FIELDS or database_state.get("schemaVersion") != 1:
        raise ConnectivityProbeParameterError("database private state fields differ")
    if set(artifact_state) != ARTIFACT_STATE_FIELDS or artifact_state.get("schemaVersion") != 1:
        raise ConnectivityProbeParameterError("artifact private state fields differ")
    if (
        database_state.get("resourceGroupName") != config["databaseResourceGroupName"]
        or SQL_SERVER_PATTERN.fullmatch(str(database_state.get("serverName", ""))) is None
        or artifact_state.get("resourceGroupName") != config["artifactResourceGroupName"]
    ):
        raise ConnectivityProbeParameterError("private state belongs to another deployment")
    try:
        image_reference = image_reference_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ConnectivityProbeParameterError("connectivity probe image is unreadable") from error
    expiry = parse_expiration(expires_at, allow_runtime_expiry=False)
    artifact_expiry = parse_expiration(artifact_state.get("expiresAt"), allow_runtime_expiry=False)
    if expiry > artifact_expiry:
        raise ConnectivityProbeParameterError("connectivity probe outlives the artifact registry")
    values: dict[str, Any] = {
        **{name: value for name, value in config.items() if name != "schemaVersion"},
        "imageReference": image_reference,
        "registryServer": artifact_state["loginServer"],
        "sqlServerHostname": f"{database_state['serverName']}.database.windows.net",
        "expiresAt": expiry,
    }
    validate_materialized_values(values)
    output.write_text(
        json.dumps(
            {
                "$schema": PARAMETER_SCHEMA,
                "contentVersion": "1.0.0.0",
                "parameters": {name: {"value": value} for name, value in values.items()},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(output, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("config", type=Path)
    get_parser = commands.add_parser("get-config")
    get_parser.add_argument("config", type=Path)
    get_parser.add_argument("name", choices=sorted(EXPECTED_CONFIG_FIELDS - {"schemaVersion", "additionalTags"}))
    materialize_parser = commands.add_parser("materialize")
    materialize_parser.add_argument("config", type=Path)
    materialize_parser.add_argument("output", type=Path)
    materialize_parser.add_argument("--database-state", type=Path, required=True)
    materialize_parser.add_argument("--artifact-state", type=Path, required=True)
    materialize_parser.add_argument("--image-reference-file", type=Path, required=True)
    materialize_parser.add_argument("--expires-at", required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            load_config(args.config)
            print("PASS connectivity probe configuration is deterministic and identifier-free")
        elif args.command == "get-config":
            print(load_config(args.config)[args.name])
        else:
            materialize(
                args.config,
                args.database_state,
                args.artifact_state,
                args.image_reference_file,
                args.output,
                args.expires_at,
            )
            print("PASS materialized owner-only connectivity probe parameters", file=sys.stderr)
        return 0
    except ConnectivityProbeParameterError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())