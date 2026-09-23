#!/usr/bin/env python3
"""Validate connectivity-probe dependencies without modifying Azure."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from connectivity_probe_parameters import ConnectivityProbeParameterError
from connectivity_probe_parameters import load_config
from connectivity_probe_parameters import parameter_values
from database_network_parameters import DatabaseNetworkParameterError
from database_network_parameters import parameter_values as network_parameter_values
from runtime_parameters import ParameterError
from runtime_parameters import parameter_values as runtime_parameter_values


class ConnectivityProbePreflightError(RuntimeError):
    pass


def normalize_location(value: Any) -> str:
    return "".join(str(value).split()).casefold()


def parse_utc(value: Any) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ConnectivityProbePreflightError("connectivity probe expiration is invalid") from error
    if parsed.utcoffset() != dt.timedelta(0):
        raise ConnectivityProbePreflightError("connectivity probe expiration must use UTC")
    return parsed


def az_json(arguments: list[str], *, timeout: int = 60) -> Any:
    try:
        result = subprocess.run(
            ["az", *arguments, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ConnectivityProbePreflightError("connectivity probe readiness query failed") from error
    if result.returncode != 0:
        raise ConnectivityProbePreflightError("connectivity probe readiness query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise ConnectivityProbePreflightError("connectivity probe readiness returned unreadable JSON") from error


def validate_local(
    config_path: Path,
    parameters_path: Path,
    runtime_path: Path,
    network_path: Path,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    try:
        config = load_config(config_path)
        values = parameter_values(parameters_path)
        runtime = runtime_parameter_values(runtime_path, allow_runtime_expiry=False)
        network = network_parameter_values(network_path)
    except (ConnectivityProbeParameterError, ParameterError, DatabaseNetworkParameterError) as error:
        raise ConnectivityProbePreflightError(str(error)) from error
    for name in (
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
    ):
        if config[name] != values[name]:
            raise ConnectivityProbePreflightError("connectivity probe config and parameters differ")
    for name in (
        "location",
        "environment",
        "projectName",
        "platformResourceGroupName",
        "containerAppsEnvironmentName",
        "ownerTag",
        "additionalTags",
    ):
        if config[name] != runtime[name]:
            raise ConnectivityProbePreflightError("connectivity probe and runtime contracts differ")
    if (
        network["location"] != config["location"]
        or network["environment"] != config["environment"]
        or network["projectName"] != config["projectName"]
        or network["platformResourceGroupName"] != config["platformResourceGroupName"]
        or network["databaseResourceGroupName"] != config["databaseResourceGroupName"]
        or f"{network['sqlServerName']}.database.windows.net" != values["sqlServerHostname"]
    ):
        raise ConnectivityProbePreflightError("connectivity probe and database network contracts differ")
    current = now or dt.datetime.now(dt.UTC)
    expires = parse_utc(values["expiresAt"])
    if expires <= current or expires > current + dt.timedelta(hours=4):
        raise ConnectivityProbePreflightError("connectivity probe expiration is outside its four-hour maximum")
    if expires > parse_utc(runtime["expiresAt"]) or expires > parse_utc(network["expiresAt"]):
        raise ConnectivityProbePreflightError("connectivity probe outlives its runtime or network dependency")
    return values


def validate_azure(values: dict[str, Any]) -> None:
    account = az_json(["account", "show"], timeout=20)
    expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
    if account.get("environmentName") != "AzureCloud" or (
        expected_subscription and account.get("id") != expected_subscription
    ):
        raise ConnectivityProbePreflightError("active Azure account or cloud differs")
    environment = az_json(
        [
            "containerapp",
            "env",
            "show",
            "--resource-group",
            values["platformResourceGroupName"],
            "--name",
            values["containerAppsEnvironmentName"],
        ],
        timeout=45,
    )
    environment_state = environment.get("provisioningState") or (environment.get("properties") or {}).get(
        "provisioningState"
    )
    if environment_state != "Succeeded" or normalize_location(environment.get("location")) != normalize_location(
        values["location"]
    ):
        raise ConnectivityProbePreflightError("Container Apps environment or region differs")
    registries = az_json(["acr", "list", "--resource-group", values["artifactResourceGroupName"]], timeout=30)
    matching_registries = [
        registry for registry in registries if registry.get("loginServer") == values["registryServer"]
    ]
    if (
        len(matching_registries) != 1
        or matching_registries[0].get("adminUserEnabled") is not False
        or matching_registries[0].get("anonymousPullEnabled") not in (None, False)
    ):
        raise ConnectivityProbePreflightError("connectivity probe registry differs")
    servers = az_json(
        ["sql", "server", "list", "--resource-group", values["databaseResourceGroupName"]],
        timeout=30,
    )
    if (
        len(servers) != 1
        or servers[0].get("fullyQualifiedDomainName") != values["sqlServerHostname"]
        or servers[0].get("publicNetworkAccess") != "Disabled"
    ):
        raise ConnectivityProbePreflightError("private SQL target differs")
    if az_json(["group", "exists", "--name", values["probeResourceGroupName"]], timeout=20) is True:
        raise ConnectivityProbePreflightError("a connectivity probe resource group already exists")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--runtime-parameters", type=Path, required=True)
    parser.add_argument("--network-parameters", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    try:
        values = validate_local(args.config, args.parameters, args.runtime_parameters, args.network_parameters)
        print("PASS connectivity probe, runtime, and private-network contracts agree")
        if args.offline:
            print("PASS offline connectivity probe preflight complete")
            return 0
        if shutil.which("az") is None:
            raise ConnectivityProbePreflightError("Azure CLI is not installed")
        validate_azure(values)
        print("PASS same-region Container Apps, private SQL, and registry dependencies are ready")
        print("INFO no resources were created or changed")
        return 0
    except ConnectivityProbePreflightError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())