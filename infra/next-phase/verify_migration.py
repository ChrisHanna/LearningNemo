#!/usr/bin/env python3
"""Verify the exact migration job and one successful hash-checking execution."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from migration_contract import classify
from migration_contract import job_name
from migration_contract import tags
from migration_contract import tags_match
from migration_parameters import MigrationParameterError
from migration_parameters import parameter_values


class MigrationVerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MigrationVerificationError(message)


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
        raise MigrationVerificationError("migration verification query failed") from error
    if result.returncode != 0:
        raise MigrationVerificationError("migration verification query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise MigrationVerificationError("migration verification returned unreadable JSON") from error


def validate_job(job: dict[str, Any], values: dict[str, Any], expected_client_id: str) -> None:
    require(tags_match(job.get("tags"), tags(values)), "migration job tags differ")
    identity = job.get("identity") or {}
    identities = identity.get("userAssignedIdentities") or {}
    expected_identity_suffix = (
        f"/resourceGroups/{values['databaseResourceGroupName']}/providers/"
        f"Microsoft.ManagedIdentity/userAssignedIdentities/id-{values['projectName']}-sql-admin-{values['environment']}"
    ).casefold()
    require(
        identity.get("type") == "UserAssigned"
        and len(identities) == 1
        and next(iter(identities), "").casefold().endswith(expected_identity_suffix),
        "migration job identity differs",
    )
    properties = job.get("properties") or {}
    expected_environment = (
        f"/resourceGroups/{values['platformResourceGroupName']}/providers/Microsoft.App/"
        f"managedEnvironments/{values['containerAppsEnvironmentName']}"
    ).casefold()
    require(str(properties.get("environmentId", "")).casefold().endswith(expected_environment), "migration environment differs")
    configuration = properties.get("configuration") or {}
    manual = configuration.get("manualTriggerConfig") or {}
    require(
        properties.get("workloadProfileName") == "Consumption"
        and configuration.get("triggerType") == "Manual"
        and configuration.get("replicaTimeout") == 600
        and configuration.get("replicaRetryLimit") == 0
        and manual.get("parallelism") == 1
        and manual.get("replicaCompletionCount") == 1,
        "migration execution bounds differ",
    )
    registries = configuration.get("registries") or []
    require(
        len(registries) == 1
        and registries[0].get("server") == values["registryServer"]
        and str(registries[0].get("identity", "")).casefold().endswith(expected_identity_suffix)
        and not registries[0].get("username")
        and not registries[0].get("passwordSecretRef"),
        "migration registry identity differs",
    )
    require({item.get("name") for item in configuration.get("secrets") or []} == {"migration-bundle"}, "migration secret inventory differs")
    containers = ((properties.get("template") or {}).get("containers") or [])
    require(len(containers) == 1 and containers[0].get("image") == values["imageReference"], "migration image differs")
    container = containers[0]
    require(
        container.get("command") == ["python3"]
        and container.get("args") == ["/opt/learningnemo/scripts/apply-sql-migrations.py"],
        "migration command differs",
    )
    environment = {item.get("name"): item.get("value") for item in container.get("env") or []}
    require(
        environment
        == {
            "LEARNINGNEMO_SQL_MIGRATION_BUNDLE": "/var/run/learningnemo/migrations.sql",
            "LEARNINGNEMO_SQL_MIGRATION_SHA256": values["migrationBundleSha256"],
            "LEARNINGNEMO_SQL_SERVER": values["sqlServerHostname"],
            "LEARNINGNEMO_SQL_DATABASE": values["databaseName"],
            "AZURE_CLIENT_ID": expected_client_id,
        },
        "migration runtime environment differs",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--execution-name")
    args = parser.parse_args()
    try:
        try:
            values = parameter_values(args.parameters)
        except MigrationParameterError as error:
            raise MigrationVerificationError(str(error)) from error
        account = az_json(["account", "show"], timeout=20)
        expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
        require(not expected_subscription or account.get("id") == expected_subscription, "active subscription mismatch")
        group_name = values["migrationResourceGroupName"]
        group = az_json(["group", "show", "--name", group_name], timeout=20)
        require(group.get("location") == values["location"] and tags_match(group.get("tags"), tags(values)), "migration group differs")
        resources = az_json(["resource", "list", "--resource-group", group_name], timeout=30)
        require(classify(resources) == "complete", "migration inventory differs")
        identity = az_json(
            [
                "identity",
                "show",
                "--resource-group",
                values["databaseResourceGroupName"],
                "--name",
                f"id-{values['projectName']}-sql-admin-{values['environment']}",
            ],
            timeout=30,
        )
        expected_client_id = str(identity.get("clientId", ""))
        require(expected_client_id != "", "migration identity client ID is absent")
        job = az_json(
            ["containerapp", "job", "show", "--resource-group", group_name, "--name", job_name(values)],
            timeout=45,
        )
        validate_job(job, values, expected_client_id)
        executions = az_json(
            ["containerapp", "job", "execution", "list", "--resource-group", group_name, "--name", job_name(values)],
            timeout=45,
        )
        matching = [
            item
            for item in executions
            if args.execution_name is None or item.get("name") == args.execution_name
        ]
        require(len(matching) >= 1, "migration execution is absent")
        execution = matching[0]
        status = (execution.get("properties") or {}).get("status") or execution.get("status")
        require(status == "Succeeded", "migration execution did not succeed")
        print("PASS exact one-shot migration job identity, digest, secret mount, and bounds match")
        print("PASS hash-checking migration execution succeeded")
        return 0
    except MigrationVerificationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())