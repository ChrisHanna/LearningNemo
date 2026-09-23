#!/usr/bin/env python3
"""Validate live control-cycle dependencies without modifying Azure."""

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

from control_cycle_parameters import ControlCycleParameterError
from control_cycle_parameters import load_config
from control_cycle_parameters import parameter_values
from database_network_parameters import DatabaseNetworkParameterError
from database_network_parameters import parameter_values as network_parameter_values
from runtime_parameters import ParameterError
from runtime_parameters import parameter_values as runtime_parameter_values
from workload_parameters import WorkloadParameterError
from workload_parameters import load_config as load_workload_config
from workload_parameters import parameter_values as workload_parameter_values


class ControlCyclePreflightError(RuntimeError):
    pass


def parse_utc(value: Any) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ControlCyclePreflightError("control-cycle expiration is invalid") from error
    if parsed.utcoffset() != dt.timedelta(0):
        raise ControlCyclePreflightError("control-cycle expiration must use UTC")
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
        raise ControlCyclePreflightError("control-cycle readiness query failed") from error
    if result.returncode != 0:
        raise ControlCyclePreflightError("control-cycle readiness query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise ControlCyclePreflightError("control-cycle readiness returned unreadable JSON") from error


def validate_local(
    config_path: Path,
    parameters_path: Path,
    runtime_path: Path,
    network_path: Path,
    workload_config_path: Path,
    workload_parameters_path: Path,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    try:
        config = load_config(config_path)
        values = parameter_values(parameters_path)
        runtime = runtime_parameter_values(runtime_path, allow_runtime_expiry=False)
        network = network_parameter_values(network_path)
        workload_config = load_workload_config(workload_config_path)
        workload = workload_parameter_values(workload_parameters_path)
    except (
        ControlCycleParameterError,
        ParameterError,
        DatabaseNetworkParameterError,
        WorkloadParameterError,
    ) as error:
        raise ControlCyclePreflightError(str(error)) from error
    if any(config[name] != values[name] for name in config if name != "schemaVersion"):
        raise ControlCyclePreflightError("control-cycle config and parameters differ")
    shared_runtime = (
        "location",
        "environment",
        "projectName",
        "platformResourceGroupName",
        "containerAppsEnvironmentName",
        "ownerTag",
        "additionalTags",
    )
    if any(config[name] != runtime[name] for name in shared_runtime):
        raise ControlCyclePreflightError("control-cycle and runtime contracts differ")
    shared_workload = (
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
    )
    if any(config[name] != workload_config[name] for name in shared_workload):
        raise ControlCyclePreflightError("control-cycle and worker configuration differ")
    if (
        values["imageReference"] != workload["imageReference"]
        or values["serviceApplicationIds"] != workload["serviceApplicationIds"]
        or values["registryServer"] != workload["registryServer"]
        or values["sqlServerHostname"] != workload["sqlServerHostname"]
    ):
        raise ControlCyclePreflightError("control-cycle and deployed worker release differ")
    if (
        network["location"] != config["location"]
        or network["environment"] != config["environment"]
        or network["projectName"] != config["projectName"]
        or network["platformResourceGroupName"] != config["platformResourceGroupName"]
        or network["databaseResourceGroupName"] != config["databaseResourceGroupName"]
        or f"{network['sqlServerName']}.database.windows.net" != values["sqlServerHostname"]
    ):
        raise ControlCyclePreflightError("control-cycle and database network contracts differ")
    current = now or dt.datetime.now(dt.UTC)
    expires = parse_utc(values["expiresAt"])
    if expires <= current or expires > current + dt.timedelta(hours=4):
        raise ControlCyclePreflightError("control-cycle expiration is outside its four-hour maximum")
    dependency_expirations = (
        parse_utc(runtime["expiresAt"]),
        parse_utc(network["expiresAt"]),
        parse_utc(workload["expiresAt"]),
    )
    if any(expires > dependency for dependency in dependency_expirations):
        raise ControlCyclePreflightError("control-cycle outlives a runtime, network, or worker dependency")
    return values


def validate_azure(values: dict[str, Any]) -> None:
    account = az_json(["account", "show"], timeout=20)
    expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
    if account.get("environmentName") != "AzureCloud" or (
        expected_subscription and account.get("id") != expected_subscription
    ):
        raise ControlCyclePreflightError("active Azure account or cloud differs")
    if az_json(["group", "exists", "--name", values["controlResourceGroupName"]], timeout=20) is True:
        raise ControlCyclePreflightError("a control-cycle resource group already exists")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--runtime-parameters", type=Path, required=True)
    parser.add_argument("--network-parameters", type=Path, required=True)
    parser.add_argument("--workload-config", type=Path, required=True)
    parser.add_argument("--workload-parameters", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    try:
        values = validate_local(
            args.config,
            args.parameters,
            args.runtime_parameters,
            args.network_parameters,
            args.workload_config,
            args.workload_parameters,
        )
        print("PASS control-cycle release and dependency lifetimes agree")
        if args.offline:
            print("PASS offline control-cycle preflight complete")
            return 0
        if shutil.which("az") is None:
            raise ControlCyclePreflightError("Azure CLI is not installed")
        validate_azure(values)
        print("PASS control-cycle resource group is absent and ready for reviewed creation")
        print("INFO no resources were created or changed")
        return 0
    except ControlCyclePreflightError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())