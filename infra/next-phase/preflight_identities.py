#!/usr/bin/env python3
"""Read-only Azure preflight for the persistent WP2a identity slice."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from platform_contract import expected_resource_tags
from platform_contract import foundation_inventory
from platform_contract import identity_inventory
from platform_contract import platform_group_tags
from platform_contract import platform_inventory
from platform_contract import resource_identity
from platform_contract import runtime_inventory
from platform_parameters import ParameterError
from platform_parameters import parameter_values


PROVIDER_CONTRACT = Path(__file__).with_name("provider-phases.json")


class PreflightError(RuntimeError):
    pass


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
        raise PreflightError("an Azure identity readiness query failed or timed out") from error
    if result.returncode != 0:
        raise PreflightError("an Azure identity readiness query failed; refresh `az login` and retry")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise PreflightError("Azure CLI returned an unreadable identity readiness response") from error


def required_providers() -> tuple[str, ...]:
    try:
        document = json.loads(PROVIDER_CONTRACT.read_text(encoding="utf-8"))
        providers = tuple(document["phases"]["identities"])
        if document.get("schemaVersion") != 1 or not providers:
            raise ValueError
        if not all(isinstance(name, str) and name.startswith("Microsoft.") for name in providers):
            raise ValueError
        return providers
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise PreflightError("identity provider phase contract is invalid") from error


def tags_match(actual: Any, expected: dict[str, str]) -> bool:
    return isinstance(actual, dict) and all(actual.get(key) == value for key, value in expected.items())


def classify_identity_state(values: dict[str, Any], resources: list[dict[str, Any]]) -> str:
    actual = {
        resource_identity(resource.get("type", ""), resource.get("name", ""))
        for resource in resources
    }
    foundation = foundation_inventory(values)
    identities = identity_inventory(values)
    allowed = platform_inventory(values)
    if not foundation <= actual or not actual <= allowed:
        return "unexpected"
    present_identities = actual & identities
    if not present_identities:
        return "empty"
    if present_identities == identities:
        return "complete"
    return "partial"


def validate_azure(values: dict[str, Any]) -> None:
    account = az_json(["account", "show"], timeout=20)
    if account.get("environmentName") != "AzureCloud":
        raise PreflightError("the active Azure CLI cloud must be AzureCloud")
    expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
    if expected_subscription and account.get("id") != expected_subscription:
        raise PreflightError("the active subscription does not match AZURE_SUBSCRIPTION_ID")
    locations = az_json(["account", "list-locations"], timeout=30)
    if values["location"] not in {item.get("name") for item in locations}:
        raise PreflightError("the identity location is unavailable to the active subscription")
    providers = az_json(["provider", "list"], timeout=60)
    states = {item.get("namespace"): item.get("registrationState") for item in providers}
    missing = [name for name in required_providers() if states.get(name) != "Registered"]
    if missing:
        raise PreflightError(f"register required identity provider(s): {', '.join(missing)}")
    provider = az_json(["provider", "show", "--namespace", "Microsoft.ManagedIdentity"], timeout=45)
    resource_types = [
        item
        for item in provider.get("resourceTypes") or []
        if str(item.get("resourceType", "")).casefold() == "userassignedidentities"
    ]
    normalized_location = values["location"].replace(" ", "").casefold()
    if len(resource_types) != 1 or normalized_location not in {
        str(location).replace(" ", "").casefold()
        for location in resource_types[0].get("locations") or []
    }:
        raise PreflightError("regional managed identities are unavailable in the selected location")

    resource_group = values["platformResourceGroupName"]
    if az_json(["group", "exists", "--name", resource_group], timeout=20) is not True:
        raise PreflightError("WP1 platform resource group must exist before identity deployment")
    group = az_json(["group", "show", "--name", resource_group], timeout=20)
    if group.get("location") != values["location"] or not tags_match(group.get("tags"), platform_group_tags(values)):
        raise PreflightError("WP1 platform resource group location or ownership tags differ")
    resources = az_json(["resource", "list", "--resource-group", resource_group], timeout=30)
    state = classify_identity_state(values, resources)
    if state == "unexpected":
        raise PreflightError("platform resource inventory is outside the exact WP1/WP2a contract")
    expected_tags = expected_resource_tags(values)
    for resource in resources:
        identity = resource_identity(resource.get("type", ""), resource.get("name", ""))
        if not tags_match(resource.get("tags"), expected_tags[identity]):
            raise PreflightError("platform resource ownership or phase tags differ")
    runtime_present = bool(
        {
            resource_identity(resource.get("type", ""), resource.get("name", ""))
            for resource in resources
        }
        & runtime_inventory(values)
    )
    print("PASS identity providers and regional resource type are available")
    print(f"PASS platform identity slice has {state} state")
    if runtime_present:
        print("INFO runtime envelope already exists and is outside this identity-only mutation")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parameters",
        type=Path,
        default=Path(__file__).parent / "environments" / "dev.platform.parameters.json",
    )
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    try:
        try:
            values = parameter_values(args.parameters)
        except ParameterError as error:
            raise PreflightError(str(error)) from error
        required_providers()
        print("PASS identity names, tags, and parameter contract are structurally valid")
        if args.offline:
            print("PASS offline identity preflight complete; no Azure calls were made")
            return 0
        validate_azure(values)
        print("PASS read-only identity Azure preflight complete")
        print("INFO no resources were created or changed")
        return 0
    except PreflightError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())