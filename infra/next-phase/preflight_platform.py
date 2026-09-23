#!/usr/bin/env python3
"""Read-only configuration and Azure preflight for the WP2a platform base."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from platform_contract import expected_resource_tags
from platform_contract import foundation_inventory
from platform_contract import identity_inventory
from platform_contract import infrastructure_resource_group_name
from platform_contract import platform_group_tags
from platform_contract import resource_identity
from platform_contract import runtime_inventory
from platform_contract import runtime_resource_tags
from runtime_parameters import ParameterError
from runtime_parameters import parameter_values
from workload_contract import workload_inventory


PROVIDER_CONTRACT = Path(__file__).with_name("provider-phases.json")


class PreflightError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parameters",
        type=Path,
        default=Path(__file__).parent / "environments" / "dev.runtime.parameters.json",
    )
    parser.add_argument("--offline", action="store_true", help="Validate configuration without Azure calls.")
    parser.add_argument(
        "--require-budget",
        action="store_true",
        help="Fail unless an active subscription budget has an enabled notification.",
    )
    parser.add_argument(
        "--allow-workloads",
        action="store_true",
        help="Allow exactly the four known WP2b apps while reconciling the runtime envelope.",
    )
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
        raise PreflightError("an Azure CLI readiness check failed or timed out") from error
    if result.returncode != 0:
        raise PreflightError("an Azure CLI readiness check failed; refresh `az login` and retry")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PreflightError("Azure CLI returned an unreadable readiness response") from error


def required_providers() -> tuple[str, ...]:
    try:
        document = json.loads(PROVIDER_CONTRACT.read_text(encoding="utf-8"))
        providers = document["phases"]["platform"]
        if document.get("schemaVersion") != 1 or not isinstance(providers, list):
            raise ValueError
        result = tuple(providers)
        if not result or not all(isinstance(name, str) and name.startswith("Microsoft.") for name in result):
            raise ValueError
        return result
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise PreflightError("provider phase contract is invalid") from error


def normalize_location(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def validate_account(location: str) -> dict[str, Any]:
    account = az_json(["account", "show"], timeout=20)
    if account.get("environmentName") != "AzureCloud":
        raise PreflightError("the active Azure CLI cloud must be AzureCloud")
    expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
    if expected_subscription and account.get("id") != expected_subscription:
        raise PreflightError("the active subscription does not match AZURE_SUBSCRIPTION_ID")
    locations = az_json(["account", "list-locations"], timeout=30)
    if location not in {item.get("name") for item in locations}:
        raise PreflightError(f"location {location!r} is unavailable to the active subscription")
    return account


def budget_is_active(budget: dict[str, Any], today: dt.date) -> bool:
    properties = budget.get("properties") or {}
    notifications = properties.get("notifications") or {}
    if not isinstance(properties.get("amount"), (int, float)) or properties["amount"] <= 0:
        return False
    if not any(
        isinstance(notification, dict) and notification.get("enabled") is True
        for notification in notifications.values()
    ):
        return False
    period = properties.get("timePeriod") or {}
    try:
        start = dt.datetime.fromisoformat(str(period["startDate"]).replace("Z", "+00:00")).date()
        end_value = period.get("endDate")
        end = (
            dt.datetime.fromisoformat(str(end_value).replace("Z", "+00:00")).date()
            if end_value
            else None
        )
    except (KeyError, ValueError):
        return False
    return start <= today and (end is None or today <= end)


def budget_meets_ceiling(
    budget: dict[str, Any],
    today: dt.date,
    maximum_amount: int,
) -> bool:
    properties = budget.get("properties") or {}
    return budget_is_active(budget, today) and properties["amount"] <= maximum_amount


def validate_budget(account: dict[str, Any], *, required: bool, maximum_amount: int) -> None:
    subscription_id = str(account.get("id", ""))
    if not subscription_id:
        raise PreflightError("the active subscription identifier is unavailable")
    try:
        response = az_json(
            [
                "rest",
                "--method",
                "GET",
                "--url",
                (
                    "https://management.azure.com/subscriptions/"
                    f"{subscription_id}/providers/Microsoft.Consumption/budgets"
                    "?api-version=2024-08-01"
                ),
            ],
            timeout=45,
        )
    except PreflightError as error:
        if required:
            raise PreflightError("unable to verify the required subscription budget") from error
        print("WARN subscription budget readiness could not be verified")
        return
    budgets = response.get("value") if isinstance(response, dict) else None
    active = [
        budget
        for budget in budgets or []
        if isinstance(budget, dict)
        and budget_meets_ceiling(
            budget,
            dt.datetime.now(dt.UTC).date(),
            maximum_amount,
        )
    ]
    if not active:
        if required:
            raise PreflightError("a notified subscription budget at or below the WP2a cost ceiling is required before apply")
        print("WARN no notified subscription budget meets the WP2a cost ceiling; apply will be blocked")
        return
    print("PASS a notified subscription budget meets the WP2a cost ceiling")


def validate_providers_and_locations(location: str) -> None:
    providers = az_json(["provider", "list"], timeout=60)
    states = {item.get("namespace"): item.get("registrationState") for item in providers}
    missing = [name for name in required_providers() if states.get(name) != "Registered"]
    if missing:
        raise PreflightError(f"register required WP2a provider(s): {', '.join(missing)}")

    expected_types = {
        "Microsoft.App": "managedEnvironments",
    }
    requested_location = normalize_location(location)
    for namespace, resource_type in expected_types.items():
        provider = az_json(["provider", "show", "--namespace", namespace], timeout=45)
        matches = [
            item
            for item in provider.get("resourceTypes") or []
            if str(item.get("resourceType", "")).casefold() == resource_type.casefold()
        ]
        if len(matches) != 1:
            raise PreflightError(f"could not resolve {namespace}/{resource_type} regional metadata")
        locations = {normalize_location(str(value)) for value in matches[0].get("locations") or []}
        if requested_location not in locations:
            raise PreflightError(f"{namespace}/{resource_type} is unavailable in {location}")
    print("PASS providers and regional resource types required by WP2a are available")


def classify_platform_inventory(
    resources: list[dict[str, Any]],
    expected: set[tuple[str, str]],
    required: set[tuple[str, str]] | None = None,
    allowed: set[tuple[str, str]] | None = None,
) -> str:
    actual = {
        resource_identity(item.get("type", ""), item.get("name", ""))
        for item in resources
    }
    normalized_expected = {resource_identity(resource_type, name) for resource_type, name in expected}
    normalized_required = {
        resource_identity(resource_type, name) for resource_type, name in (required or set())
    }
    normalized_allowed = {
        resource_identity(resource_type, name) for resource_type, name in (allowed or set())
    }
    if not normalized_required <= actual or not actual <= normalized_required | normalized_expected | normalized_allowed:
        return "unexpected"
    phase_resources = actual - normalized_required - normalized_allowed
    if not phase_resources:
        return "empty"
    if phase_resources == normalized_expected:
        return "complete"
    if phase_resources < normalized_expected:
        return "partial"
    return "unexpected"


def tags_match(actual: Any, expected: dict[str, str]) -> bool:
    return isinstance(actual, dict) and all(actual.get(key) == value for key, value in expected.items())


def validate_platform_group(values: dict[str, Any], *, allow_workloads: bool = False) -> None:
    resource_group = values["platformResourceGroupName"]
    exists = az_json(["group", "exists", "--name", resource_group], timeout=20)
    if exists is not True:
        raise PreflightError("WP1 platform resource group must exist before WP2a")
    group = az_json(["group", "show", "--name", resource_group], timeout=20)
    expected_group_tags = platform_group_tags(values)
    if group.get("location") != values["location"] or not tags_match(group.get("tags"), expected_group_tags):
        raise PreflightError("WP1 platform resource group location or ownership tags differ")

    resources = az_json(["resource", "list", "--resource-group", resource_group], timeout=30)
    persistent = foundation_inventory(values) | identity_inventory(values)
    expected = runtime_inventory(values)
    optional = workload_inventory(values) if allow_workloads else set()
    state = classify_platform_inventory(resources, expected, persistent, optional)
    if state == "unexpected":
        raise PreflightError("platform group resource inventory is outside the exact WP1/WP2a contract")
    expected_tags = expected_resource_tags(values)
    runtime_identities = runtime_inventory(values)
    live_runtime_expiration = None
    for resource in resources:
        identity = resource_identity(resource.get("type", ""), resource.get("name", ""))
        if identity in optional:
            continue
        required_tags = dict(expected_tags[identity])
        if identity in runtime_identities:
            required_tags.pop("expiresAt", None)
            live_runtime_expiration = (resource.get("tags") or {}).get("expiresAt")
            try:
                parsed_expiration = dt.datetime.fromisoformat(
                    str(live_runtime_expiration).replace("Z", "+00:00")
                )
            except ValueError as error:
                raise PreflightError("runtime expiration tag is missing or malformed") from error
            if parsed_expiration.utcoffset() != dt.timedelta(0):
                raise PreflightError("runtime expiration tag must use UTC")
        if not tags_match(resource.get("tags"), required_tags):
            raise PreflightError("platform resource ownership or phase tags differ")

    managed_group = infrastructure_resource_group_name(values)
    managed_group_exists = az_json(["group", "exists", "--name", managed_group], timeout=20)
    environment_identity = resource_identity(
        "Microsoft.App/managedEnvironments",
        values["containerAppsEnvironmentName"],
    )
    environment_exists = environment_identity in {
        resource_identity(resource.get("type", ""), resource.get("name", ""))
        for resource in resources
    }
    if managed_group_exists is not environment_exists:
        raise PreflightError("Container Apps environment and managed infrastructure group state differ")
    if managed_group_exists:
        managed_group_state = az_json(["group", "show", "--name", managed_group], timeout=20)
        required_managed_tags = runtime_resource_tags(values)
        required_managed_tags.pop("expiresAt", None)
        if not tags_match(managed_group_state.get("tags"), required_managed_tags):
            raise PreflightError("managed infrastructure group tags differ from the WP2a contract")
        if (managed_group_state.get("tags") or {}).get("expiresAt") != live_runtime_expiration:
            raise PreflightError("managed infrastructure group expiration differs from the runtime environment")
    if state in {"empty", "partial"}:
        print(f"WARN runtime envelope has {state} state; review what-if before apply")
    else:
        print("PASS runtime envelope has a complete resource inventory")
    print("PASS WP1 network and persistent identity dependencies match")


def main() -> int:
    args = parse_args()
    try:
        try:
            values = parameter_values(args.parameters)
        except ParameterError as error:
            raise PreflightError(str(error)) from error
        print("PASS WP2a names, tags, and parameter contract are structurally valid")
        if args.offline:
            required_providers()
            print("PASS offline platform preflight complete; no Azure calls were made")
            return 0
        if shutil.which("az") is None:
            raise PreflightError("Azure CLI is not installed")
        account = validate_account(values["location"])
        print(f"PASS active AzureCloud subscription supports {values['location']}")
        validate_budget(
            account,
            required=args.require_budget,
            maximum_amount=values["monthlyCostCeiling"],
        )
        validate_providers_and_locations(values["location"])
        validate_platform_group(values, allow_workloads=args.allow_workloads)
        print("PASS read-only WP2a Azure preflight complete")
        print("INFO no resources were created or changed")
        return 0
    except PreflightError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())