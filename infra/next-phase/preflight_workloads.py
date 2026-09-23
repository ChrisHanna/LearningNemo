#!/usr/bin/env python3
"""Read-only local and Azure readiness checks for WP2b trusted workers."""

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

from artifact_parameters import ArtifactParameterError
from artifact_parameters import parameter_values as artifact_parameter_values
from database_network_parameters import DatabaseNetworkParameterError
from database_network_parameters import parameter_values as network_parameter_values
from platform_contract import expected_resource_tags
from platform_contract import platform_group_tags
from platform_contract import platform_inventory
from platform_contract import resource_identity
from preflight_platform import PreflightError as PlatformPreflightError
from preflight_platform import validate_budget
from runtime_parameters import ParameterError
from runtime_parameters import parameter_values as runtime_parameter_values
from workload_contract import SERVICE_KEYS
from workload_contract import combined_values
from workload_contract import identity_name
from workload_contract import workload_auth_inventory
from workload_contract import workload_inventory
from workload_parameters import WorkloadParameterError
from workload_parameters import load_config
from workload_parameters import parameter_values as workload_parameter_values


ENVIRONMENT_API_VERSION = "2026-01-01"
IDENTITY_API_VERSION = "2024-11-30"
MAX_WORKLOAD_LIFETIME = dt.timedelta(hours=24)


class WorkloadPreflightError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--runtime-parameters", type=Path, required=True)
    parser.add_argument("--artifact-parameters", type=Path, required=True)
    parser.add_argument("--network-parameters", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--require-budget", action="store_true")
    return parser.parse_args()


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
        raise WorkloadPreflightError("an Azure readiness query failed or timed out") from error
    if result.returncode != 0:
        raise WorkloadPreflightError("an Azure readiness query failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise WorkloadPreflightError("Azure returned an unreadable readiness response") from error


def parse_utc(value: Any, label: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise WorkloadPreflightError(f"{label} is missing or malformed") from error
    if parsed.utcoffset() != dt.timedelta(0) or parsed.microsecond:
        raise WorkloadPreflightError(f"{label} must be a whole-second UTC timestamp")
    return parsed


def validate_local_inputs(
    config_path: Path,
    parameters_path: Path,
    runtime_parameters_path: Path,
    *,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        config = load_config(config_path)
        workload = workload_parameter_values(parameters_path)
        runtime = runtime_parameter_values(runtime_parameters_path)
        values = combined_values(config, workload)
    except (WorkloadParameterError, ParameterError, ValueError) as error:
        raise WorkloadPreflightError(str(error)) from error
    shared = (
        "location",
        "environment",
        "projectName",
        "platformResourceGroupName",
        "containerAppsEnvironmentName",
        "ownerTag",
        "additionalTags",
    )
    if any(config[name] != runtime[name] for name in shared):
        raise WorkloadPreflightError("workload and WP2a runtime configuration differ")
    current = now or dt.datetime.now(dt.UTC)
    if current.utcoffset() != dt.timedelta(0):
        raise WorkloadPreflightError("preflight clock must use UTC")
    expiration = parse_utc(values["expiresAt"], "workload expiresAt")
    if expiration <= current:
        raise WorkloadPreflightError("workload expiresAt is not in the future")
    if expiration > current + MAX_WORKLOAD_LIFETIME:
        raise WorkloadPreflightError("workload lifetime exceeds 24 hours")
    return values, runtime


def tags_match(actual: Any, expected: dict[str, str]) -> bool:
    return isinstance(actual, dict) and all(actual.get(key) == value for key, value in expected.items())


def validate_dependency_expirations(
    values: dict[str, Any],
    artifact_parameters_path: Path,
    network_parameters_path: Path,
) -> None:
    try:
        artifacts = artifact_parameter_values(artifact_parameters_path)
        network = network_parameter_values(network_parameters_path)
    except (ArtifactParameterError, DatabaseNetworkParameterError) as error:
        raise WorkloadPreflightError(str(error)) from error
    if (
        artifacts["artifactResourceGroupName"] != values["artifactResourceGroupName"]
        or network["databaseResourceGroupName"] != values["databaseResourceGroupName"]
        or f"{network['sqlServerName']}.database.windows.net" != values["sqlServerHostname"]
    ):
        raise WorkloadPreflightError("workload artifact or database network ownership differs")
    for dependency in (artifacts, network):
        for name in ("environment", "projectName", "ownerTag", "additionalTags"):
            if dependency[name] != values[name]:
                raise WorkloadPreflightError("workload dependency contract differs")
    workload_expiration = parse_utc(values["expiresAt"], "workload expiresAt")
    if workload_expiration > parse_utc(artifacts["expiresAt"], "artifact expiresAt"):
        raise WorkloadPreflightError("workload expiration exceeds artifact registry expiration")
    if workload_expiration > parse_utc(network["expiresAt"], "database network expiresAt"):
        raise WorkloadPreflightError("workload expiration exceeds database network expiration")


def validate_account(values: dict[str, Any]) -> dict[str, Any]:
    account = az_json(["account", "show"], timeout=20)
    if account.get("environmentName") != "AzureCloud":
        raise WorkloadPreflightError("the active Azure CLI cloud must be AzureCloud")
    expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
    if expected_subscription and account.get("id") != expected_subscription:
        raise WorkloadPreflightError("the active subscription does not match AZURE_SUBSCRIPTION_ID")
    if account.get("tenantId") != values["tenantId"]:
        raise WorkloadPreflightError("the active tenant does not match the private workload parameters")
    return account


def resource_show(resource_group: str, name: str, resource_type: str, api_version: str) -> dict[str, Any]:
    resource = az_json(
        [
            "resource",
            "show",
            "--resource-group",
            resource_group,
            "--name",
            name,
            "--resource-type",
            resource_type,
            "--api-version",
            api_version,
        ]
    )
    if not isinstance(resource, dict):
        raise WorkloadPreflightError("Azure resource query returned an unexpected shape")
    return resource


def validate_audience_application(application_id: str) -> None:
    application = az_json(["ad", "app", "show", "--id", application_id], timeout=30)
    if application.get("appId") != application_id:
        raise WorkloadPreflightError("a trusted service audience application differs")
    if application.get("signInAudience") != "AzureADMyOrg":
        raise WorkloadPreflightError("trusted service audiences must be tenant-only")
    if set(application.get("identifierUris") or []) != {f"api://{application_id}"}:
        raise WorkloadPreflightError("a trusted service audience URI differs")
    if (application.get("api") or {}).get("requestedAccessTokenVersion") != 2:
        raise WorkloadPreflightError("trusted service audiences must issue v2 access tokens")
    if application.get("passwordCredentials") or application.get("keyCredentials"):
        raise WorkloadPreflightError("trusted service audience applications must not hold credentials")
    for surface in ("publicClient", "spa", "web"):
        if ((application.get(surface) or {}).get("redirectUris") or []):
            raise WorkloadPreflightError("trusted service audiences must not expose redirect URIs")
    principal = az_json(["ad", "sp", "show", "--id", application_id], timeout=30)
    if principal.get("appId") != application_id or principal.get("accountEnabled") is not True:
        raise WorkloadPreflightError("a trusted service audience principal is absent or disabled")


def validate_azure_state(
    values: dict[str, Any],
    runtime: dict[str, Any],
    *,
    require_budget: bool,
) -> None:
    account = validate_account(values)
    try:
        validate_budget(
            account,
            required=require_budget,
            maximum_amount=runtime["monthlyCostCeiling"],
        )
    except PlatformPreflightError as error:
        raise WorkloadPreflightError(str(error)) from error
    providers = az_json(["provider", "list"], timeout=60)
    states = {item.get("namespace"): item.get("registrationState") for item in providers}
    if states.get("Microsoft.App") != "Registered":
        raise WorkloadPreflightError("Microsoft.App must be registered")

    resource_group = values["platformResourceGroupName"]
    group = az_json(["group", "show", "--name", resource_group], timeout=20)
    if group.get("location") != values["location"] or not tags_match(
        group.get("tags"), platform_group_tags(runtime)
    ):
        raise WorkloadPreflightError("platform resource group ownership or location differs")
    resources = az_json(["resource", "list", "--resource-group", resource_group], timeout=30)
    actual = {
        resource_identity(resource.get("type", ""), resource.get("name", ""))
        for resource in resources
    }
    required = platform_inventory(runtime)
    allowed_workloads = workload_inventory(values)
    if not required <= actual:
        raise WorkloadPreflightError("the complete WP1/WP2a platform must exist before workloads")
    if not actual <= required | allowed_workloads | workload_auth_inventory(values):
        raise WorkloadPreflightError("platform group contains a resource outside the WP1/WP2 contract")
    present_workloads = actual & allowed_workloads
    if present_workloads and present_workloads != allowed_workloads:
        raise WorkloadPreflightError("trusted worker inventory is partial; remove it before redeploying")
    expected_tags = expected_resource_tags(runtime)
    for resource in resources:
        identity = resource_identity(resource.get("type", ""), resource.get("name", ""))
        if identity in expected_tags:
            tags = dict(expected_tags[identity])
            if identity in platform_inventory(runtime) and "expiresAt" in tags:
                tags.pop("expiresAt")
            if not tags_match(resource.get("tags"), tags):
                raise WorkloadPreflightError("a WP1/WP2a dependency has unexpected ownership tags")

    environment = resource_show(
        resource_group,
        values["containerAppsEnvironmentName"],
        "Microsoft.App/managedEnvironments",
        ENVIRONMENT_API_VERSION,
    )
    runtime_expiration = parse_utc((environment.get("tags") or {}).get("expiresAt"), "runtime expiresAt")
    if runtime_expiration <= dt.datetime.now(dt.UTC):
        raise WorkloadPreflightError("the Container Apps runtime envelope has expired")
    workload_expiration = parse_utc(values["expiresAt"], "workload expiresAt")
    if workload_expiration > runtime_expiration:
        raise WorkloadPreflightError("workload expiration exceeds the runtime envelope expiration")
    if (environment.get("properties") or {}).get("provisioningState") != "Succeeded":
        raise WorkloadPreflightError("the Container Apps runtime envelope is not ready")

    registries = az_json(
        ["acr", "list", "--resource-group", values["artifactResourceGroupName"]],
        timeout=30,
    )
    if (
        len(registries) != 1
        or registries[0].get("loginServer") != values["registryServer"]
        or registries[0].get("adminUserEnabled") is not False
        or registries[0].get("anonymousPullEnabled") not in (None, False)
    ):
        raise WorkloadPreflightError("the exact managed-identity artifact registry is not ready")
    sql_servers = az_json(
        ["sql", "server", "list", "--resource-group", values["databaseResourceGroupName"]],
        timeout=30,
    )
    if (
        len(sql_servers) != 1
        or sql_servers[0].get("fullyQualifiedDomainName") != values["sqlServerHostname"]
        or sql_servers[0].get("publicNetworkAccess") != "Disabled"
    ):
        raise WorkloadPreflightError("the exact private-only SQL server is not ready")
    database = az_json(
        [
            "sql",
            "db",
            "show",
            "--resource-group",
            values["databaseResourceGroupName"],
            "--server",
            values["sqlServerHostname"].split(".", 1)[0],
            "--name",
            values["databaseName"],
        ],
        timeout=45,
    )
    if database.get("status") not in {"Online", "Paused", "Resuming"}:
        raise WorkloadPreflightError("the WP3 database is not available")

    project = values["projectName"]
    environment_name = values["environment"]
    control = resource_show(
        resource_group,
        f"id-{project}-control-{environment_name}",
        "Microsoft.ManagedIdentity/userAssignedIdentities",
        IDENTITY_API_VERSION,
    )
    control_properties = control.get("properties") or {}
    if (
        control_properties.get("clientId") != values["controlCallerApplicationId"]
        or control_properties.get("principalId") != values["controlCallerPrincipalId"]
    ):
        raise WorkloadPreflightError("private caller identifiers do not match the control managed identity")
    for mode in SERVICE_KEYS:
        resource_show(
            resource_group,
            identity_name(values, mode),
            "Microsoft.ManagedIdentity/userAssignedIdentities",
            IDENTITY_API_VERSION,
        )
        validate_audience_application(values["serviceApplicationIds"][SERVICE_KEYS[mode]])


def main() -> int:
    args = parse_args()
    try:
        values, runtime = validate_local_inputs(
            args.config,
            args.parameters,
            args.runtime_parameters,
        )
        validate_dependency_expirations(
            values,
            args.artifact_parameters,
            args.network_parameters,
        )
        print("PASS workload names, private identifiers, image digest, and TTL are structurally valid")
        if args.offline:
            print("PASS offline WP2b preflight complete; no Azure calls were made")
            return 0
        if shutil.which("az") is None:
            raise WorkloadPreflightError("Azure CLI is not installed")
        validate_azure_state(values, runtime, require_budget=args.require_budget)
        print("PASS runtime, private SQL, ACR, identities, and four Entra audiences match")
        print("PASS read-only WP2b Azure preflight complete")
        print("INFO no resources were created or changed")
        return 0
    except WorkloadPreflightError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())