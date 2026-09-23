#!/usr/bin/env python3
"""Validate SAW/OpenShell config and materialize owner-only ARM parameters."""

from __future__ import annotations

import argparse
import datetime as dt
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
    "sawResourceGroupName",
    "platformResourceGroupName",
    "sawVnetName",
    "workspaceSubnetName",
    "vmName",
    "vmSize",
    "adminUsername",
    "imagePublisher",
    "imageOffer",
    "imageSku",
    "imageVersion",
    "openShellVersion",
    "openShellPackageUrl",
    "openShellPackageSha256",
    "sandboxImageReference",
    "supervisorImageReference",
    "sandboxVcpus",
    "sandboxMemoryMiB",
    "sandboxOverlayMiB",
    "ownerTag",
    "additionalTags",
}
BICEP_CONFIG_FIELDS = EXPECTED_CONFIG_FIELDS - {"schemaVersion"}
EXPECTED_PARAMETERS = BICEP_CONFIG_FIELDS | {
    "sshPublicKey",
    "expiresAt",
    "sqlServerHostname",
}
BASE_PARAMETERS = {
    "location",
    "environment",
    "projectName",
    "sawResourceGroupName",
    "sawVnetName",
    "workspaceSubnetName",
    "vmName",
    "vmSize",
    "adminUsername",
    "sshPublicKey",
    "imagePublisher",
    "imageOffer",
    "imageSku",
    "imageVersion",
    "expiresAt",
    "openShellVersion",
    "ownerTag",
    "additionalTags",
}
BOOTSTRAP_PARAMETERS = {
    "location",
    "environment",
    "projectName",
    "platformResourceGroupName",
    "vmName",
    "adminUsername",
    "expiresAt",
    "openShellVersion",
    "openShellPackageUrl",
    "openShellPackageSha256",
    "sandboxImageReference",
    "supervisorImageReference",
    "sandboxVcpus",
    "sandboxMemoryMiB",
    "sandboxOverlayMiB",
    "sqlServerHostname",
}
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._()\-]{1,90}$")
SSH_KEY_PATTERN = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/]{40,}={0,3}(?: [A-Za-z0-9._@-]{1,64})?$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IMAGE_PATTERN = re.compile(r"^ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$")
SQL_HOST_PATTERN = re.compile(r"^[a-z0-9-]{1,63}\.database\.windows\.net$")
PACKAGE_URL = "https://github.com/NVIDIA/OpenShell/releases/download/v0.0.116/openshell_0.0.116-1_amd64.deb"
PACKAGE_SHA256 = "883b5223399dd30a2b33b7f76817fd81295d610ce6f2d04a0266fc7cfbf87575"
SANDBOX_IMAGE = "ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:c2a43bb0d765774e2790b3babfb20997bb2eac7b4bf4c6d7d8661e99817bf904"
SUPERVISOR_IMAGE = "ghcr.io/nvidia/openshell/supervisor@sha256:1f02f37ee9e16c3b1245b80899a954fc8198099494d4fe0cd1e891fc63adf127"
RESERVED_TAGS = {
    "costprofile",
    "disposable",
    "environment",
    "expiresat",
    "identitypurpose",
    "managedby",
    "openshelldriver",
    "openshellversion",
    "owner",
    "platformphase",
    "project",
    "sawmaturity",
    "trustzone",
}


class WorkspaceParameterError(RuntimeError):
    pass


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkspaceParameterError(f"unable to read {label}") from error
    if not isinstance(value, dict):
        raise WorkspaceParameterError(f"{label} must contain an object")
    return value


def load_config_document(config: dict[str, Any]) -> dict[str, Any]:
    if set(config) != EXPECTED_CONFIG_FIELDS or config.get("schemaVersion") != 1:
        raise WorkspaceParameterError("workspace configuration fields differ")
    string_fields = EXPECTED_CONFIG_FIELDS - {
        "schemaVersion",
        "sandboxVcpus",
        "sandboxMemoryMiB",
        "sandboxOverlayMiB",
        "additionalTags",
    }
    if any(not isinstance(config.get(name), str) or not config[name] for name in string_fields):
        raise WorkspaceParameterError("workspace string configuration differs")
    for name in (
        "environment",
        "projectName",
        "sawResourceGroupName",
        "platformResourceGroupName",
        "sawVnetName",
        "workspaceSubnetName",
        "vmName",
        "vmSize",
        "adminUsername",
    ):
        if NAME_PATTERN.fullmatch(config[name]) is None:
            raise WorkspaceParameterError(f"workspace field {name} is invalid")
    if SAFE_ALIAS_PATTERN.fullmatch(config["ownerTag"]) is None:
        raise WorkspaceParameterError("workspace ownerTag is invalid")
    if (
        config["location"] != "eastus"
        or config["vmSize"] != "Standard_D2s_v5"
        or config["imagePublisher"] != "Canonical"
        or config["imageOffer"] != "ubuntu-24_04-lts"
        or config["imageSku"] != "server"
        or config["imageVersion"] != "24.04.202608270"
        or config["openShellVersion"] != "0.0.116"
        or config["openShellPackageUrl"] != PACKAGE_URL
        or config["openShellPackageSha256"] != PACKAGE_SHA256
        or config["sandboxImageReference"] != SANDBOX_IMAGE
        or config["supervisorImageReference"] != SUPERVISOR_IMAGE
    ):
        raise WorkspaceParameterError("workspace host or OpenShell supply-chain pin differs")
    if (
        config["sandboxVcpus"] != 1
        or config["sandboxMemoryMiB"] != 1024
        or config["sandboxOverlayMiB"] != 2048
    ):
        raise WorkspaceParameterError("workspace MicroVM resource bounds differ")
    tags = config["additionalTags"]
    if not isinstance(tags, dict):
        raise WorkspaceParameterError("workspace additionalTags must be an object")
    for name, value in tags.items():
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or not name
            or not value
            or name.casefold() in RESERVED_TAGS
            or any(fragment.casefold() in name.casefold() for fragment in SECRET_NAME_FRAGMENTS)
        ):
            raise WorkspaceParameterError("workspace additionalTags are invalid")
    return config


def load_config(path: Path) -> dict[str, Any]:
    return load_config_document(read_json(path, "workspace configuration"))


def validate_materialized_values(values: dict[str, Any]) -> None:
    config = {
        "schemaVersion": 1,
        **{name: values[name] for name in BICEP_CONFIG_FIELDS},
    }
    load_config_document(config)
    if SSH_KEY_PATTERN.fullmatch(str(values["sshPublicKey"])) is None:
        raise WorkspaceParameterError("workspace SSH public key is invalid")
    if SQL_HOST_PATTERN.fullmatch(str(values["sqlServerHostname"])) is None:
        raise WorkspaceParameterError("workspace SQL hostname is invalid")
    expires = dt.datetime.fromisoformat(parse_expiration(values["expiresAt"], allow_runtime_expiry=False).replace("Z", "+00:00"))
    now = dt.datetime.now(dt.UTC)
    if expires <= now or expires > now + dt.timedelta(hours=4, minutes=5):
        raise WorkspaceParameterError("workspace expiration is outside its four-hour maximum")


def parameter_values(path: Path) -> dict[str, Any]:
    document = read_json(path, "workspace parameters")
    parameters = document.get("parameters")
    if (
        document.get("$schema") != PARAMETER_SCHEMA
        or document.get("contentVersion") != "1.0.0.0"
        or not isinstance(parameters, dict)
        or set(parameters) != EXPECTED_PARAMETERS
        or any(not isinstance(entry, dict) or set(entry) != {"value"} for entry in parameters.values())
    ):
        raise WorkspaceParameterError("workspace parameter contract differs")
    values = {name: entry["value"] for name, entry in parameters.items()}
    validate_materialized_values(values)
    return values


def materialize(
    config_path: Path,
    database_state_path: Path,
    ssh_public_key_path: Path,
    output: Path,
    expires_at: str,
) -> None:
    config = load_config(config_path)
    database_state = read_json(database_state_path, "database private state")
    try:
        ssh_public_key = ssh_public_key_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise WorkspaceParameterError("workspace SSH public key is unreadable") from error
    if (
        database_state.get("schemaVersion") != 1
        or database_state.get("resourceGroupName") != "rg-learningnemo-data-dev"
        or not isinstance(database_state.get("serverName"), str)
    ):
        raise WorkspaceParameterError("database private state differs from the workspace contract")
    values = {
        **{name: config[name] for name in BICEP_CONFIG_FIELDS},
        "sshPublicKey": ssh_public_key,
        "expiresAt": parse_expiration(expires_at, allow_runtime_expiry=False),
        "sqlServerHostname": f"{database_state['serverName']}.database.windows.net",
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


def project(source: Path, output: Path, kind: str) -> None:
    values = parameter_values(source)
    names = BASE_PARAMETERS if kind == "base" else BOOTSTRAP_PARAMETERS
    output.write_text(
        json.dumps(
            {
                "$schema": PARAMETER_SCHEMA,
                "contentVersion": "1.0.0.0",
                "parameters": {name: {"value": values[name]} for name in sorted(names)},
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
    materialize_parser.add_argument("--ssh-public-key", type=Path, required=True)
    materialize_parser.add_argument("--expires-at", required=True)
    project_parser = commands.add_parser("project")
    project_parser.add_argument("source", type=Path)
    project_parser.add_argument("output", type=Path)
    project_parser.add_argument("--kind", choices=("base", "bootstrap"), required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            load_config(args.config)
            print("PASS workspace configuration pins the host and OpenShell supply chain")
        elif args.command == "get-config":
            print(load_config(args.config)[args.name])
        elif args.command == "materialize":
            materialize(
                args.config,
                args.database_state,
                args.ssh_public_key,
                args.output,
                args.expires_at,
            )
            print("PASS materialized owner-only SAW workspace parameters", file=sys.stderr)
        else:
            project(args.source, args.output, args.kind)
            print(f"PASS projected owner-only {args.kind} workspace parameters", file=sys.stderr)
        return 0
    except WorkspaceParameterError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())