#!/usr/bin/env python3
"""Verify the live least-authority connectivity probe before execution."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from connectivity_probe_parameters import ConnectivityProbeParameterError
from connectivity_probe_parameters import parameter_values


ACR_PULL_ROLE_ID = "7f951dda-4ed3-4680-a7ca-43fe172d538d"


class ConnectivityProbeVerificationError(RuntimeError):
    pass


def normalize_location(value: Any) -> str:
    return "".join(str(value).split()).casefold()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ConnectivityProbeVerificationError(message)


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
        raise ConnectivityProbeVerificationError("connectivity probe verification query failed") from error
    if result.returncode != 0:
        raise ConnectivityProbeVerificationError("connectivity probe verification query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise ConnectivityProbeVerificationError("connectivity probe verification returned unreadable JSON") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--stack-name", required=True)
    args = parser.parse_args()
    try:
        try:
            values = parameter_values(args.parameters)
        except ConnectivityProbeParameterError as error:
            raise ConnectivityProbeVerificationError(str(error)) from error
        account = az_json(["account", "show"], timeout=20)
        expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
        require(not expected_subscription or account.get("id") == expected_subscription, "active subscription differs")
        stack = az_json(["stack", "sub", "show", "--name", args.stack_name], timeout=30)
        stack_state = stack.get("provisioningState") or (stack.get("properties") or {}).get("provisioningState")
        require(str(stack_state).casefold() == "succeeded", "connectivity probe deployment stack is not ready")
        resource_group = values["probeResourceGroupName"]
        identity_name = f"id-{values['projectName']}-diagnostic-{values['environment']}"
        job_name = f"caj-{values['projectName']}-netprobe-{values['environment']}"
        resources = az_json(["resource", "list", "--resource-group", resource_group], timeout=30)
        resource_types = sorted(str(resource.get("type", "")).casefold() for resource in resources)
        require(resource_types == ["microsoft.app/jobs"], "connectivity probe resource inventory differs")
        identity = az_json(
            [
                "identity",
                "show",
                "--resource-group",
                values["platformResourceGroupName"],
                "--name",
                identity_name,
            ],
            timeout=30,
        )
        require(
            normalize_location(identity.get("location")) == normalize_location(values["location"]),
            "connectivity probe identity region differs",
        )
        require(identity.get("isolationScope") == "Regional", "connectivity probe identity isolation differs")
        job = az_json(
            ["containerapp", "job", "show", "--resource-group", resource_group, "--name", job_name],
            timeout=45,
        )
        properties = job.get("properties") or {}
        configuration = properties.get("configuration") or {}
        manual = configuration.get("manualTriggerConfig") or {}
        require(
            normalize_location(job.get("location")) == normalize_location(values["location"]),
            "connectivity probe job region differs",
        )
        require(
            properties.get("workloadProfileName") == "Consumption"
            and configuration.get("triggerType") == "Manual"
            and configuration.get("replicaTimeout") == 180
            and configuration.get("replicaRetryLimit") == 0
            and manual.get("parallelism") == 1
            and manual.get("replicaCompletionCount") == 1,
            "connectivity probe execution bounds differ",
        )
        require(configuration.get("secrets") in ([], None), "connectivity probe contains secrets")
        registries = configuration.get("registries") or []
        registry_identity = str(registries[0].get("identity", "")) if len(registries) == 1 else ""
        identity_kind = (
            "resource-id"
            if registry_identity.casefold().startswith("/subscriptions/")
            else "client-id"
            if registry_identity == identity.get("clientId")
            else "system"
            if registry_identity.casefold() == "system"
            else "other"
        )
        print(f"INFO connectivity probe registry identity representation: {identity_kind}")
        require(
            len(registries) == 1
            and registries[0].get("server") == values["registryServer"]
            and (
                registry_identity.casefold() == str(identity.get("id", "")).casefold()
                or registry_identity == identity.get("clientId")
            ),
            "connectivity probe registry identity differs",
        )
        containers = ((properties.get("template") or {}).get("containers") or [])
        require(len(containers) == 1, "connectivity probe container inventory differs")
        container = containers[0]
        require(container.get("image") == values["imageReference"], "connectivity probe image digest differs")
        require(
            container.get("command") == ["/usr/local/bin/python3"]
            and container.get("args") == ["/opt/learningnemo/scripts/probe-sql-odbc.py"],
            "connectivity probe command differs",
        )
        environment = {item.get("name"): item.get("value") for item in container.get("env") or []}
        require(
            environment
            == {
                "LEARNINGNEMO_SQL_SERVER": values["sqlServerHostname"],
                "LEARNINGNEMO_SQL_DATABASE": "master",
                "AZURE_CLIENT_ID": identity["clientId"],
            },
            "connectivity probe environment differs",
        )
        registries = az_json(["acr", "list", "--resource-group", values["artifactResourceGroupName"]], timeout=30)
        matching_registries = [
            registry for registry in registries if registry.get("loginServer") == values["registryServer"]
        ]
        require(len(matching_registries) == 1, "connectivity probe registry inventory differs")
        registry = matching_registries[0]
        assignments = az_json(
            [
                "role",
                "assignment",
                "list",
                "--scope",
                registry["id"],
                "--assignee-object-id",
                identity["principalId"],
            ],
            timeout=45,
        )
        require(
            len(assignments) == 1
            and str(assignments[0].get("roleDefinitionId", "")).casefold().endswith(f"/{ACR_PULL_ROLE_ID}"),
            "connectivity probe AcrPull assignment differs",
        )
        serialized = json.dumps(job, sort_keys=True).casefold()
        require("ingress" not in serialized and "sql-admin" not in serialized, "connectivity probe authority differs")
        print("PASS live connectivity probe reuses the same-region Regional diagnostic identity with one AcrPull")
        print("PASS live connectivity probe uses the signed digest and has no SQL authority or ingress")
        return 0
    except ConnectivityProbeVerificationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())