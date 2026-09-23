#!/usr/bin/env python3
"""Validate non-secret workload config and materialize private ARM parameters."""

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
    "platformResourceGroupName",
    "containerAppsEnvironmentName",
    "artifactResourceGroupName",
    "databaseResourceGroupName",
    "databaseName",
    "ownerTag",
    "additionalTags",
}
EXPECTED_AUTH_FIELDS = {
    "tenantId",
    "controlCallerApplicationId",
    "controlCallerPrincipalId",
    "serviceApplicationIds",
}
EXPECTED_SERVICES = {"diagnostic", "queryRunner", "remediation", "verifier"}
EXPECTED_PARAMETERS = {
    "location",
    "environment",
    "projectName",
    "containerAppsEnvironmentName",
    "databaseName",
    "imageReference",
    "registryServer",
    "sqlServerHostname",
    "expiresAt",
    "ownerTag",
    "tenantId",
    "controlCallerApplicationId",
    "controlCallerPrincipalId",
    "serviceApplicationIds",
    "additionalTags",
}
SAFE_CONFIG_OUTPUTS = {
    "location",
    "environment",
    "projectName",
    "platformResourceGroupName",
    "containerAppsEnvironmentName",
    "artifactResourceGroupName",
    "databaseResourceGroupName",
    "databaseName",
    "ownerTag",
}
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
IMAGE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$")
REGISTRY_PATTERN = re.compile(r"^[a-z0-9]{5,50}\.azurecr\.io$")
SQL_SERVER_PATTERN = re.compile(r"^[a-z0-9-]{1,63}\.database\.windows\.net$")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._()\-]{1,90}$")
RESERVED_TAGS = {
    "costprofile",
    "disposable",
    "environment",
    "expiresat",
    "imagedigest",
    "managedby",
    "owner",
    "platformphase",
    "project",
    "servicemode",
    "trustzone",
}


class WorkloadParameterError(RuntimeError):
    pass


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkloadParameterError(f"unable to read {label}") from error
    if not isinstance(value, dict):
        raise WorkloadParameterError(f"{label} must contain a JSON object")
    return value


def load_config_document(config: dict[str, Any]) -> dict[str, Any]:
    if set(config) != EXPECTED_CONFIG_FIELDS or config.get("schemaVersion") != 1:
        raise WorkloadParameterError("workload configuration fields differ from the contract")
    for name in EXPECTED_CONFIG_FIELDS - {"schemaVersion", "additionalTags"}:
        value = config.get(name)
        if not isinstance(value, str) or not value:
            raise WorkloadParameterError(f"workload configuration field {name} must be a string")
    for name in (
        "environment",
        "projectName",
        "platformResourceGroupName",
        "containerAppsEnvironmentName",
        "artifactResourceGroupName",
        "databaseResourceGroupName",
        "databaseName",
    ):
        if NAME_PATTERN.fullmatch(config[name]) is None:
            raise WorkloadParameterError(f"workload configuration field {name} has invalid characters")
    if SAFE_ALIAS_PATTERN.fullmatch(config["ownerTag"]) is None:
        raise WorkloadParameterError("ownerTag must be a non-sensitive alias")
    tags = config.get("additionalTags")
    if not isinstance(tags, dict):
        raise WorkloadParameterError("additionalTags must be an object")
    for name, value in tags.items():
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or not name
            or not value
            or name.casefold() in RESERVED_TAGS
            or any(fragment.casefold() in name.casefold() for fragment in SECRET_NAME_FRAGMENTS)
        ):
            raise WorkloadParameterError("additionalTags contains an invalid or managed value")
    return config


def load_config(path: Path) -> dict[str, Any]:
    return load_config_document(read_json(path, "workload configuration"))


def load_auth_document(auth: dict[str, Any]) -> dict[str, Any]:
    if set(auth) != EXPECTED_AUTH_FIELDS:
        raise WorkloadParameterError("runtime workload identity fields differ from the contract")
    service_ids = auth.get("serviceApplicationIds")
    if not isinstance(service_ids, dict) or set(service_ids) != EXPECTED_SERVICES:
        raise WorkloadParameterError("service application identity fields differ from the contract")
    values = [
        auth.get("tenantId"),
        auth.get("controlCallerApplicationId"),
        auth.get("controlCallerPrincipalId"),
        *service_ids.values(),
    ]
    if any(not isinstance(value, str) or UUID_PATTERN.fullmatch(value) is None for value in values):
        raise WorkloadParameterError("runtime workload identity contains an invalid identifier")
    if len(set(service_ids.values())) != len(EXPECTED_SERVICES):
        raise WorkloadParameterError("each trusted service must use a distinct application audience")
    return auth


def load_auth(path: Path) -> dict[str, Any]:
    return load_auth_document(read_json(path, "runtime workload identity configuration"))


def validate_image_reference(value: str) -> str:
    if IMAGE_PATTERN.fullmatch(value) is None:
        raise WorkloadParameterError("imageReference must be a lowercase repository@sha256 digest")
    return value


def load_materialized(path: Path) -> dict[str, Any]:
    document = read_json(path, "materialized workload parameters")
    parameters = document.get("parameters")
    if (
        document.get("$schema") != PARAMETER_SCHEMA
        or document.get("contentVersion") != "1.0.0.0"
        or not isinstance(parameters, dict)
        or set(parameters) != EXPECTED_PARAMETERS
    ):
        raise WorkloadParameterError("materialized workload parameter contract differs")
    if any(not isinstance(entry, dict) or set(entry) != {"value"} for entry in parameters.values()):
        raise WorkloadParameterError("each materialized workload parameter must contain only a value")
    values = {name: entry["value"] for name, entry in parameters.items()}
    load_config_document(
        {
            "schemaVersion": 1,
            "location": values["location"],
            "environment": values["environment"],
            "projectName": values["projectName"],
            "platformResourceGroupName": "validation-placeholder",
            "containerAppsEnvironmentName": values["containerAppsEnvironmentName"],
            "artifactResourceGroupName": "validation-placeholder",
            "databaseResourceGroupName": "validation-placeholder",
            "databaseName": values["databaseName"],
            "ownerTag": values["ownerTag"],
            "additionalTags": values["additionalTags"],
        }
    )
    load_auth_document(
        {
            "tenantId": values["tenantId"],
            "controlCallerApplicationId": values["controlCallerApplicationId"],
            "controlCallerPrincipalId": values["controlCallerPrincipalId"],
            "serviceApplicationIds": values["serviceApplicationIds"],
        }
    )
    image_reference = validate_image_reference(values["imageReference"])
    if REGISTRY_PATTERN.fullmatch(str(values["registryServer"])) is None or not image_reference.startswith(
        f"{values['registryServer']}/"
    ):
        raise WorkloadParameterError("workload image and registry state differ")
    if SQL_SERVER_PATTERN.fullmatch(str(values["sqlServerHostname"])) is None:
        raise WorkloadParameterError("workload SQL server hostname is invalid")
    parse_expiration(values["expiresAt"], allow_runtime_expiry=False)
    return document


def parameter_values(path: Path) -> dict[str, Any]:
    document = load_materialized(path)
    return {name: entry["value"] for name, entry in document["parameters"].items()}


def materialize(
    config_path: Path,
    auth_path: Path,
    image_reference_path: Path,
    output: Path,
    expires_at: str,
    database_state_path: Path,
    artifact_state_path: Path,
) -> None:
    config = load_config(config_path)
    auth = load_auth(auth_path)
    try:
        image_reference = image_reference_path.read_text(encoding="utf-8").strip()
        database_state = read_json(database_state_path, "database private state")
        artifact_state = read_json(artifact_state_path, "artifact private state")
    except OSError as error:
        raise WorkloadParameterError("unable to read image reference") from error
    if (
        set(database_state)
        != {"schemaVersion", "resourceGroupName", "serverName", "databaseName", "sqlAdminIdentityName"}
        or database_state.get("schemaVersion") != 1
        or database_state.get("resourceGroupName") != config["databaseResourceGroupName"]
        or database_state.get("databaseName") != config["databaseName"]
    ):
        raise WorkloadParameterError("database private state differs from workload configuration")
    if (
        set(artifact_state)
        != {"schemaVersion", "resourceGroupName", "registryName", "loginServer", "expiresAt"}
        or artifact_state.get("schemaVersion") != 1
        or artifact_state.get("resourceGroupName") != config["artifactResourceGroupName"]
        or REGISTRY_PATTERN.fullmatch(str(artifact_state.get("loginServer", ""))) is None
    ):
        raise WorkloadParameterError("artifact private state differs from workload configuration")
    registry_server = str(artifact_state["loginServer"])
    image_reference = validate_image_reference(image_reference)
    if not image_reference.startswith(f"{registry_server}/"):
        raise WorkloadParameterError("workload image belongs to another registry")
    sql_server_hostname = f"{database_state['serverName']}.database.windows.net"
    if SQL_SERVER_PATTERN.fullmatch(sql_server_hostname) is None:
        raise WorkloadParameterError("database private state contains an invalid SQL server")
    normalized_expiration = parse_expiration(expires_at, allow_runtime_expiry=False)
    artifact_expiration = parse_expiration(artifact_state.get("expiresAt"), allow_runtime_expiry=False)
    if normalized_expiration > artifact_expiration:
        raise WorkloadParameterError("workload expiration exceeds artifact registry expiration")
    document = {
        "$schema": PARAMETER_SCHEMA,
        "contentVersion": "1.0.0.0",
        "parameters": {
            "location": {"value": config["location"]},
            "environment": {"value": config["environment"]},
            "projectName": {"value": config["projectName"]},
            "containerAppsEnvironmentName": {"value": config["containerAppsEnvironmentName"]},
            "databaseName": {"value": config["databaseName"]},
            "imageReference": {"value": image_reference},
            "registryServer": {"value": registry_server},
            "sqlServerHostname": {"value": sql_server_hostname},
            "expiresAt": {"value": normalized_expiration},
            "ownerTag": {"value": config["ownerTag"]},
            "tenantId": {"value": auth["tenantId"]},
            "controlCallerApplicationId": {"value": auth["controlCallerApplicationId"]},
            "controlCallerPrincipalId": {"value": auth["controlCallerPrincipalId"]},
            "serviceApplicationIds": {"value": auth["serviceApplicationIds"]},
            "additionalTags": {"value": config["additionalTags"]},
        },
    }
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("config", type=Path)
    get_config_parser = subparsers.add_parser("get-config")
    get_config_parser.add_argument("config", type=Path)
    get_config_parser.add_argument("name", choices=sorted(SAFE_CONFIG_OUTPUTS))
    get_expiration_parser = subparsers.add_parser("get-expiration")
    get_expiration_parser.add_argument("parameters", type=Path)
    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("config", type=Path)
    materialize_parser.add_argument("output", type=Path)
    materialize_parser.add_argument("--auth-file", type=Path, required=True)
    materialize_parser.add_argument("--image-reference-file", type=Path, required=True)
    materialize_parser.add_argument("--database-state", type=Path, required=True)
    materialize_parser.add_argument("--artifact-state", type=Path, required=True)
    materialize_parser.add_argument("--expires-at", required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            load_config(args.config)
            print("PASS workload configuration is valid and contains no image or identity identifiers")
        elif args.command == "get-config":
            print(load_config(args.config)[args.name])
        elif args.command == "get-expiration":
            print(parameter_values(args.parameters)["expiresAt"])
        else:
            materialize(
                args.config,
                args.auth_file,
                args.image_reference_file,
                args.output,
                args.expires_at,
                args.database_state,
                args.artifact_state,
            )
            print("PASS materialized private digest and workload identity parameters", file=sys.stderr)
        return 0
    except WorkloadParameterError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())