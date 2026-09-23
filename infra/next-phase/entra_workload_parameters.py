#!/usr/bin/env python3
"""Privately resolve Graph app IDs and the control identity for WP2b."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

from foundation_parameters import PARAMETER_SCHEMA
from workload_contract import SERVICE_KEYS
from workload_parameters import WorkloadParameterError
from workload_parameters import load_auth
from workload_parameters import load_auth_document
from workload_parameters import load_config


class EntraParameterError(RuntimeError):
    pass


def az_json(arguments: list[str], *, timeout: int = 45) -> Any:
    try:
        result = subprocess.run(
            ["az", *arguments, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise EntraParameterError("an Entra discovery query failed or timed out") from error
    if result.returncode != 0:
        raise EntraParameterError("an Entra discovery query failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise EntraParameterError("Entra discovery returned unreadable JSON") from error


def unique_name(config: dict[str, Any], mode: str) -> str:
    return f"{config['projectName']}-{mode}-{config['environment']}"


def find_application(config: dict[str, Any], mode: str) -> dict[str, Any] | None:
    name = unique_name(config, mode)
    expression = urllib.parse.quote(f"uniqueName eq '{name}'", safe="")
    response = az_json(
        [
            "rest",
            "--method",
            "GET",
            "--url",
            (
                "https://graph.microsoft.com/v1.0/applications"
                f"?$filter={expression}"
                "&$select=appId,uniqueName,displayName,tags,signInAudience,"
                "identifierUris,api,passwordCredentials,keyCredentials,publicClient,spa,web"
            ),
        ]
    )
    applications = response.get("value") if isinstance(response, dict) else None
    if not isinstance(applications, list) or len(applications) > 1:
        raise EntraParameterError("a workload audience unique name resolved ambiguously")
    if not applications:
        return None
    application = applications[0]
    if (
        not isinstance(application, dict)
        or application.get("uniqueName") != name
        or application.get("displayName") != name
        or not {"learningnemo", "wp2b-trusted-worker", mode, config["environment"]}
        <= set(application.get("tags") or [])
    ):
        raise EntraParameterError("a workload audience registration differs from the IaC contract")
    return application


def discover(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    applications = {
        mode: application
        for mode in SERVICE_KEYS
        if (application := find_application(config, mode)) is not None
    }
    if applications and set(applications) != set(SERVICE_KEYS):
        raise EntraParameterError("workload audience registrations are partial")
    return applications


def discover_complete(config: dict[str, Any], *, attempts: int = 6) -> dict[str, dict[str, Any]]:
    for attempt in range(attempts):
        try:
            applications = discover(config)
        except EntraParameterError:
            applications = {}
        if set(applications) == set(SERVICE_KEYS):
            return applications
        if attempt + 1 < attempts:
            time.sleep(5)
    raise EntraParameterError("workload audience registrations did not converge")


def write_private_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def materialize(config_path: Path, auth_output: Path, parameters_output: Path) -> None:
    config = load_config(config_path)
    account = az_json(["account", "show"], timeout=20)
    expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
    if expected_subscription and account.get("id") != expected_subscription:
        raise EntraParameterError("active subscription mismatch")
    tenant_id = account.get("tenantId")
    control = az_json(
        [
            "identity",
            "show",
            "--resource-group",
            config["platformResourceGroupName"],
            "--name",
            f"id-{config['projectName']}-control-{config['environment']}",
        ]
    )
    applications = discover_complete(config)
    auth = {
        "tenantId": tenant_id,
        "controlCallerApplicationId": control.get("clientId"),
        "controlCallerPrincipalId": control.get("principalId"),
        "serviceApplicationIds": {
            key: applications[mode].get("appId")
            for mode, key in SERVICE_KEYS.items()
        },
    }
    try:
        load_auth_document(auth)
    except WorkloadParameterError as error:
        raise EntraParameterError(str(error)) from error
    parameters = {
        "$schema": PARAMETER_SCHEMA,
        "contentVersion": "1.0.0.0",
        "parameters": {
            "environment": {"value": config["environment"]},
            "projectName": {"value": config["projectName"]},
            "serviceApplicationIds": {"value": auth["serviceApplicationIds"]},
        },
    }
    write_private_json(auth_output, auth)
    write_private_json(parameters_output, parameters)


def verify(config_path: Path, auth_path: Path) -> None:
    config = load_config(config_path)
    auth = load_auth(auth_path)
    account = az_json(["account", "show"], timeout=20)
    if account.get("tenantId") != auth["tenantId"]:
        raise EntraParameterError("active tenant differs from private audience state")
    control = az_json(
        [
            "identity",
            "show",
            "--resource-group",
            config["platformResourceGroupName"],
            "--name",
            f"id-{config['projectName']}-control-{config['environment']}",
        ]
    )
    if (
        control.get("clientId") != auth["controlCallerApplicationId"]
        or control.get("principalId") != auth["controlCallerPrincipalId"]
    ):
        raise EntraParameterError("control identity differs from private audience state")
    applications = discover(config)
    if set(applications) != set(SERVICE_KEYS):
        raise EntraParameterError("all workload audience registrations must exist")
    for mode, key in SERVICE_KEYS.items():
        application_id = auth["serviceApplicationIds"][key]
        application = applications[mode]
        if (
            application.get("appId") != application_id
            or application.get("signInAudience") != "AzureADMyOrg"
            or set(application.get("identifierUris") or []) != {f"api://{application_id}"}
            or (application.get("api") or {}).get("requestedAccessTokenVersion") != 2
            or application.get("passwordCredentials")
            or application.get("keyCredentials")
        ):
            raise EntraParameterError("a workload audience application differs from policy")
        principal = az_json(["ad", "sp", "show", "--id", application_id], timeout=30)
        if (
            principal.get("appId") != application_id
            or principal.get("accountEnabled") is not True
            or principal.get("passwordCredentials")
            or principal.get("keyCredentials")
        ):
            raise EntraParameterError("a workload audience service principal differs from policy")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status")
    status.add_argument("--config", type=Path, required=True)
    resolve = subparsers.add_parser("materialize")
    resolve.add_argument("--config", type=Path, required=True)
    resolve.add_argument("--auth-output", type=Path, required=True)
    resolve.add_argument("--parameters-output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--config", type=Path, required=True)
    verify_parser.add_argument("--auth-file", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "status":
            applications = discover(load_config(args.config))
            print("complete" if applications else "absent")
        elif args.command == "materialize":
            materialize(args.config, args.auth_output, args.parameters_output)
            print("PASS privately materialized four audience IDs and the control identity")
        else:
            verify(args.config, args.auth_file)
            print("PASS four credentialless tenant-only v2 workload audiences match")
            print("PASS control managed identity binding matches private audience state")
        return 0
    except (EntraParameterError, WorkloadParameterError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())