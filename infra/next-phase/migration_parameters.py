#!/usr/bin/env python3
"""Materialize owner-only parameters for the one-shot Azure SQL migration job."""

from __future__ import annotations

import argparse
import hashlib
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
    "migrationResourceGroupName",
    "platformResourceGroupName",
    "containerAppsEnvironmentName",
    "databaseResourceGroupName",
    "databaseName",
    "ownerTag",
    "additionalTags",
}
EXPECTED_PARAMETERS = EXPECTED_CONFIG_FIELDS - {"schemaVersion"} | {
    "imageReference",
    "registryServer",
    "sqlServerHostname",
    "migrationBundle",
    "migrationBundleSha256",
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
SERVER_PATTERN = re.compile(r"^[a-z0-9-]{1,63}$")
LOGIN_SERVER_PATTERN = re.compile(r"^[a-z0-9]{5,50}\.azurecr\.io$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
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


class MigrationParameterError(RuntimeError):
    pass


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MigrationParameterError(f"unable to read {label}") from error
    if not isinstance(value, dict):
        raise MigrationParameterError(f"{label} must contain an object")
    return value


def load_config_document(config: dict[str, Any]) -> dict[str, Any]:
    if set(config) != EXPECTED_CONFIG_FIELDS or config.get("schemaVersion") != 1:
        raise MigrationParameterError("migration configuration fields differ")
    for name in EXPECTED_CONFIG_FIELDS - {"schemaVersion", "additionalTags"}:
        value = config.get(name)
        if not isinstance(value, str) or not value:
            raise MigrationParameterError(f"migration configuration field {name} must be a string")
    for name in EXPECTED_CONFIG_FIELDS - {"schemaVersion", "location", "ownerTag", "additionalTags"}:
        if NAME_PATTERN.fullmatch(config[name]) is None:
            raise MigrationParameterError(f"migration configuration field {name} is invalid")
    if SAFE_ALIAS_PATTERN.fullmatch(config["ownerTag"]) is None:
        raise MigrationParameterError("migration ownerTag must be a non-sensitive alias")
    tags = config["additionalTags"]
    if not isinstance(tags, dict):
        raise MigrationParameterError("migration additionalTags must be an object")
    for name, value in tags.items():
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or not name
            or not value
            or name.casefold() in RESERVED_TAGS
            or any(fragment.casefold() in name.casefold() for fragment in SECRET_NAME_FRAGMENTS)
        ):
            raise MigrationParameterError("migration additionalTags contains an invalid value")
    return config


def load_config(path: Path) -> dict[str, Any]:
    return load_config_document(read_json(path, "migration configuration"))


def validate_materialized_values(values: dict[str, Any]) -> None:
    config_values = {
        "schemaVersion": 1,
        **{name: values[name] for name in EXPECTED_CONFIG_FIELDS - {"schemaVersion"}},
    }
    load_config_document(config_values)
    try:
        image_reference = validate_image_reference(values["imageReference"])
    except WorkloadParameterError as error:
        raise MigrationParameterError(str(error)) from error
    if LOGIN_SERVER_PATTERN.fullmatch(str(values["registryServer"])) is None:
        raise MigrationParameterError("migration registry server is invalid")
    if not image_reference.startswith(f"{values['registryServer']}/"):
        raise MigrationParameterError("migration image belongs to another registry")
    expected_hostname = re.compile(r"^[a-z0-9-]{1,63}\.database\.windows\.net$")
    if expected_hostname.fullmatch(str(values["sqlServerHostname"])) is None:
        raise MigrationParameterError("migration SQL hostname is invalid")
    bundle = values["migrationBundle"]
    if (
        not isinstance(bundle, str)
        or not bundle.startswith("-- LearningNeMo Azure SQL migration bundle.")
        or bundle.count("BEGIN MIGRATION") != 6
        or "__" in bundle
        or len(bundle.encode("utf-8")) > 256 * 1024
    ):
        raise MigrationParameterError("migration bundle shape or size differs")
    digest = hashlib.sha256(bundle.encode("utf-8")).hexdigest()
    if values["migrationBundleSha256"] != digest or SHA256_PATTERN.fullmatch(digest) is None:
        raise MigrationParameterError("migration bundle hash differs")
    parse_expiration(values["expiresAt"], allow_runtime_expiry=False)


def load_materialized(path: Path) -> dict[str, Any]:
    document = read_json(path, "materialized migration parameters")
    parameters = document.get("parameters")
    if (
        document.get("$schema") != PARAMETER_SCHEMA
        or document.get("contentVersion") != "1.0.0.0"
        or not isinstance(parameters, dict)
        or set(parameters) != EXPECTED_PARAMETERS
        or any(not isinstance(entry, dict) or set(entry) != {"value"} for entry in parameters.values())
    ):
        raise MigrationParameterError("materialized migration parameter contract differs")
    validate_materialized_values({name: entry["value"] for name, entry in parameters.items()})
    return document


def parameter_values(path: Path) -> dict[str, Any]:
    return {name: entry["value"] for name, entry in load_materialized(path)["parameters"].items()}


def materialize(
    config_path: Path,
    database_state_path: Path,
    artifact_state_path: Path,
    image_reference_path: Path,
    bundle_path: Path,
    output: Path,
    expires_at: str,
) -> None:
    config = load_config(config_path)
    database_state = read_json(database_state_path, "database private state")
    artifact_state = read_json(artifact_state_path, "artifact private state")
    if set(database_state) != DATABASE_STATE_FIELDS or database_state.get("schemaVersion") != 1:
        raise MigrationParameterError("database private state fields differ")
    if set(artifact_state) != ARTIFACT_STATE_FIELDS or artifact_state.get("schemaVersion") != 1:
        raise MigrationParameterError("artifact private state fields differ")
    if (
        database_state.get("resourceGroupName") != config["databaseResourceGroupName"]
        or database_state.get("databaseName") != config["databaseName"]
        or SERVER_PATTERN.fullmatch(str(database_state.get("serverName", ""))) is None
    ):
        raise MigrationParameterError("database private state belongs to another deployment")
    registry_server = str(artifact_state.get("loginServer", ""))
    if LOGIN_SERVER_PATTERN.fullmatch(registry_server) is None:
        raise MigrationParameterError("artifact private state is invalid")
    try:
        image_reference = image_reference_path.read_text(encoding="utf-8").strip()
        bundle = bundle_path.read_text(encoding="utf-8")
    except OSError as error:
        raise MigrationParameterError("unable to read migration image or bundle") from error
    normalized_expiry = parse_expiration(expires_at, allow_runtime_expiry=False)
    artifact_expiry = parse_expiration(artifact_state.get("expiresAt"), allow_runtime_expiry=False)
    if normalized_expiry > artifact_expiry:
        raise MigrationParameterError("migration expiration exceeds the artifact registry expiration")
    values: dict[str, Any] = {
        **{name: value for name, value in config.items() if name != "schemaVersion"},
        "imageReference": image_reference,
        "registryServer": registry_server,
        "sqlServerHostname": f"{database_state['serverName']}.database.windows.net",
        "migrationBundle": bundle,
        "migrationBundleSha256": hashlib.sha256(bundle.encode("utf-8")).hexdigest(),
        "expiresAt": normalized_expiry,
    }
    validate_materialized_values(values)
    document = {
        "$schema": PARAMETER_SCHEMA,
        "contentVersion": "1.0.0.0",
        "parameters": {name: {"value": value} for name, value in values.items()},
    }
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)


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
    materialize_parser.add_argument("--artifact-state", type=Path, required=True)
    materialize_parser.add_argument("--image-reference-file", type=Path, required=True)
    materialize_parser.add_argument("--bundle", type=Path, required=True)
    materialize_parser.add_argument("--expires-at", required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            load_config(args.config)
            print("PASS migration configuration contains no generated identifiers or SQL")
        elif args.command == "get-config":
            print(load_config(args.config)[args.name])
        else:
            materialize(
                args.config,
                args.database_state,
                args.artifact_state,
                args.image_reference_file,
                args.bundle,
                args.output,
                args.expires_at,
            )
            print("PASS materialized owner-only hash-bound migration job parameters", file=sys.stderr)
        return 0
    except MigrationParameterError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())