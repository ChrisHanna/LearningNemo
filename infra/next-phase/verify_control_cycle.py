#!/usr/bin/env python3
"""Verify the live disposable control-cycle job before execution."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from control_cycle_parameters import ControlCycleParameterError
from control_cycle_parameters import parameter_values


ACR_PULL_ROLE_ID = "7f951dda-4ed3-4680-a7ca-43fe172d538d"
SERVICE_NAMES = {
    "diagnostic": "diagnostic",
    "queryRunner": "query-runner",
    "remediation": "remediation",
    "verifier": "verifier",
}
HOST_PATTERN = re.compile(r"^[a-z0-9-]{2,63}(?:\.[a-z0-9-]{1,63})+\.azurecontainerapps\.io$")


class ControlCycleVerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ControlCycleVerificationError(message)


def normalize_location(value: Any) -> str:
    return "".join(str(value).split()).casefold()


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
        raise ControlCycleVerificationError("control-cycle verification query failed") from error
    if result.returncode != 0:
        raise ControlCycleVerificationError("control-cycle verification query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise ControlCycleVerificationError("control-cycle verification returned unreadable JSON") from error


def identity_representation(value: str, identity: dict[str, Any]) -> bool:
    return value.casefold() == str(identity.get("id", "")).casefold() or value == identity.get("clientId")


def parse_bound_json(value: Any, label: str) -> dict[str, str]:
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as error:
        raise ControlCycleVerificationError(f"control-cycle {label} JSON is invalid") from error
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in parsed.items()
    ):
        raise ControlCycleVerificationError(f"control-cycle {label} values differ")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--stack-name", required=True)
    args = parser.parse_args()
    try:
        try:
            values = parameter_values(args.parameters)
        except ControlCycleParameterError as error:
            raise ControlCycleVerificationError(str(error)) from error
        account = az_json(["account", "show"], timeout=20)
        expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
        require(not expected_subscription or account.get("id") == expected_subscription, "active subscription differs")
        subscription_id = str(account.get("id", ""))
        require(subscription_id != "", "active subscription is unavailable")
        stack = az_json(["stack", "sub", "show", "--name", args.stack_name], timeout=30)
        stack_state = stack.get("provisioningState") or (stack.get("properties") or {}).get("provisioningState")
        require(str(stack_state).casefold() == "succeeded", "control-cycle deployment stack is not ready")
        resource_group = values["controlResourceGroupName"]
        resources = az_json(["resource", "list", "--resource-group", resource_group], timeout=30)
        require(
            sorted(str(resource.get("type", "")).casefold() for resource in resources) == ["microsoft.app/jobs"],
            "control-cycle resource inventory differs",
        )
        platform_group = values["platformResourceGroupName"]
        control_name = f"id-{values['projectName']}-control-{values['environment']}"
        diagnostic_name = f"id-{values['projectName']}-diagnostic-{values['environment']}"
        control_identity = az_json(
            ["identity", "show", "--resource-group", platform_group, "--name", control_name],
            timeout=30,
        )
        diagnostic_identity = az_json(
            ["identity", "show", "--resource-group", platform_group, "--name", diagnostic_name],
            timeout=30,
        )
        for identity in (control_identity, diagnostic_identity):
            require(
                normalize_location(identity.get("location")) == normalize_location(values["location"])
                and identity.get("isolationScope") == "Regional",
                "control-cycle identity region or isolation differs",
            )
        job_name = f"caj-{values['projectName']}-cycle-{values['environment']}"
        job = az_json(
            ["containerapp", "job", "show", "--resource-group", resource_group, "--name", job_name],
            timeout=45,
        )
        require(normalize_location(job.get("location")) == normalize_location(values["location"]), "job region differs")
        tags = job.get("tags") or {}
        expected_tags = {
            "project": values["projectName"],
            "environment": values["environment"],
            "managedBy": "bicep-deployment-stack",
            "owner": values["ownerTag"],
            "costProfile": "one-shot-control-cycle",
            "trustZone": "trusted-platform",
            "platformPhase": "wp3-control-cycle",
            "disposable": "true",
            "expiresAt": values["expiresAt"],
            "imageDigest": values["imageReference"].rsplit("@sha256:", 1)[1],
            **values["additionalTags"],
        }
        require(all(tags.get(key) == value for key, value in expected_tags.items()), "control-cycle tags differ")
        identity = job.get("identity") or {}
        identities = {str(item).casefold() for item in (identity.get("userAssignedIdentities") or {})}
        require(
            identity.get("type") == "UserAssigned"
            and identities == {str(control_identity["id"]).casefold(), str(diagnostic_identity["id"]).casefold()},
            "control-cycle attached identities differ",
        )
        properties = job.get("properties") or {}
        expected_environment = (
            f"/subscriptions/{subscription_id}/resourceGroups/{platform_group}/providers/Microsoft.App/"
            f"managedEnvironments/{values['containerAppsEnvironmentName']}"
        )
        require(
            str(properties.get("environmentId", "")).casefold() == expected_environment.casefold(),
            "control-cycle environment differs",
        )
        configuration = properties.get("configuration") or {}
        manual = configuration.get("manualTriggerConfig") or {}
        require(
            properties.get("workloadProfileName") == "Consumption"
            and configuration.get("triggerType") == "Manual"
            and configuration.get("replicaTimeout") == 600
            and configuration.get("replicaRetryLimit") == 0
            and manual.get("parallelism") == 1
            and manual.get("replicaCompletionCount") == 1,
            "control-cycle execution bounds differ",
        )
        require(configuration.get("secrets") in ([], None), "control-cycle contains secrets")
        settings = configuration.get("identitySettings") or []
        require(
            len(settings) == 1
            and settings[0].get("lifecycle") == "Main"
            and identity_representation(str(settings[0].get("identity", "")), control_identity),
            "control-cycle runtime identity differs",
        )
        registries = configuration.get("registries") or []
        require(
            len(registries) == 1
            and registries[0].get("server") == values["registryServer"]
            and identity_representation(str(registries[0].get("identity", "")), diagnostic_identity)
            and not registries[0].get("username")
            and not registries[0].get("passwordSecretRef"),
            "control-cycle registry identity differs",
        )
        containers = ((properties.get("template") or {}).get("containers") or [])
        require(len(containers) == 1, "control-cycle container inventory differs")
        container = containers[0]
        require(container.get("image") == values["imageReference"], "control-cycle image digest differs")
        require(
            container.get("command") == ["/usr/local/bin/python3"]
            and container.get("args") == ["/opt/learningnemo/scripts/run-live-cycle-workflow.py"],
            "control-cycle command differs",
        )
        environment = {item.get("name"): item.get("value") for item in container.get("env") or []}
        expected_environment_names = {
            "LEARNINGNEMO_WORKER_ENDPOINTS",
            "LEARNINGNEMO_WORKER_AUDIENCES",
            "LEARNINGNEMO_SQL_SERVER",
            "LEARNINGNEMO_SQL_DATABASE",
            "AZURE_CLIENT_ID",
            "LEARNINGNEMO_APPROVER_HASH",
            "LEARNINGNEMO_OPERATOR_HASH",
        }
        require(set(environment) == expected_environment_names, "control-cycle environment names differ")
        require(
            environment["LEARNINGNEMO_SQL_SERVER"] == values["sqlServerHostname"]
            and environment["LEARNINGNEMO_SQL_DATABASE"] == values["databaseName"]
            and environment["AZURE_CLIENT_ID"] == control_identity["clientId"]
            and environment["LEARNINGNEMO_APPROVER_HASH"] == values["approverHash"]
            and environment["LEARNINGNEMO_OPERATOR_HASH"] == values["operatorHash"],
            "control-cycle SQL or subject binding differs",
        )
        require(
            parse_bound_json(environment["LEARNINGNEMO_WORKER_AUDIENCES"], "audience")
            == values["serviceApplicationIds"],
            "control-cycle worker audiences differ",
        )
        expected_endpoints: dict[str, str] = {}
        for key, suffix in SERVICE_NAMES.items():
            app = az_json(
                [
                    "containerapp",
                    "show",
                    "--resource-group",
                    platform_group,
                    "--name",
                    f"ca-{values['projectName']}-{suffix}-{values['environment']}",
                ],
                timeout=45,
            )
            fqdn = ((app.get("properties") or {}).get("configuration") or {}).get("ingress", {}).get("fqdn")
            require(isinstance(fqdn, str) and HOST_PATTERN.fullmatch(fqdn) is not None, "worker endpoint differs")
            expected_endpoints[key] = fqdn
        require(
            parse_bound_json(environment["LEARNINGNEMO_WORKER_ENDPOINTS"], "endpoint") == expected_endpoints,
            "control-cycle worker endpoint binding differs",
        )
        registries_live = az_json(
            ["acr", "list", "--resource-group", values["artifactResourceGroupName"]],
            timeout=30,
        )
        matching = [item for item in registries_live if item.get("loginServer") == values["registryServer"]]
        require(len(matching) == 1, "control-cycle registry inventory differs")
        registry_id = matching[0]["id"]
        diagnostic_assignments = az_json(
            [
                "role",
                "assignment",
                "list",
                "--scope",
                registry_id,
                "--assignee-object-id",
                diagnostic_identity["principalId"],
            ],
            timeout=45,
        )
        control_assignments = az_json(
            [
                "role",
                "assignment",
                "list",
                "--scope",
                registry_id,
                "--assignee-object-id",
                control_identity["principalId"],
            ],
            timeout=45,
        )
        require(
            len(diagnostic_assignments) == 1
            and str(diagnostic_assignments[0].get("roleDefinitionId", "")).casefold().endswith(
                f"/{ACR_PULL_ROLE_ID}"
            )
            and control_assignments == [],
            "control-cycle registry grants differ",
        )
        require("ingress" not in configuration, "control-cycle job exposes ingress")
        print("PASS live control-cycle job uses control for runtime and diagnostic only for image pull")
        print("PASS live control-cycle job matches the signed digest, worker endpoints, audiences, and bounds")
        print("PASS control identity has no registry grant and both reused identities remain Regional")
        return 0
    except ControlCycleVerificationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())