#!/usr/bin/env python3
"""Validate SAW/OpenShell dependencies and Azure capacity without mutation."""

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

from foundation_parameters import ParameterError as FoundationParameterError
from foundation_parameters import parameter_values as foundation_parameter_values
from preflight_platform import PreflightError as PlatformPreflightError
from preflight_platform import validate_budget
from workspace_parameters import WorkspaceParameterError
from workspace_parameters import load_config
from workspace_parameters import parameter_values


BASE_RESOURCE_TYPES = {
    "microsoft.network/networksecuritygroups",
    "microsoft.network/routetables",
    "microsoft.network/virtualnetworks",
}
REQUIRED_PROVIDERS = {
    "Microsoft.Compute",
    "Microsoft.ManagedIdentity",
    "Microsoft.Network",
    "Microsoft.Resources",
}


class WorkspacePreflightError(RuntimeError):
    pass


def parse_utc(value: Any) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise WorkspacePreflightError("workspace expiration is invalid") from error
    if parsed.utcoffset() != dt.timedelta(0):
        raise WorkspacePreflightError("workspace expiration must use UTC")
    return parsed


def az_json(arguments: list[str], *, timeout: int = 120, category: str = "workspace") -> Any:
    try:
        result = subprocess.run(
            ["az", *arguments, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkspacePreflightError(f"{category} readiness query failed or timed out") from error
    if result.returncode != 0:
        raise WorkspacePreflightError(f"{category} readiness query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise WorkspacePreflightError(f"{category} readiness returned unreadable JSON") from error


def nested_value(document: dict[str, Any], key: str) -> Any:
    value = document.get(key)
    if isinstance(value, dict) and set(value) == {"value"}:
        return value["value"]
    parameters = document.get("parameters")
    if isinstance(parameters, dict):
        entry = parameters.get(key)
        if isinstance(entry, dict) and set(entry) == {"value"}:
            return entry["value"]
    return value


def dependency_expiration(path: Path) -> dt.datetime:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkspacePreflightError("workspace dependency state is unreadable") from error
    value = nested_value(document, "expiresAt")
    if value is None:
        value = nested_value(document, "expiresOn")
        try:
            day = dt.date.fromisoformat(str(value))
        except ValueError as error:
            raise WorkspacePreflightError("workspace foundation expiration is invalid") from error
        return dt.datetime.combine(day, dt.time(23, 59, 59), tzinfo=dt.UTC)
    return parse_utc(value)


def validate_local(
    config_path: Path,
    parameters_path: Path,
    foundation_path: Path,
    dependency_paths: tuple[Path, ...],
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    try:
        config = load_config(config_path)
        values = parameter_values(parameters_path)
        foundation = foundation_parameter_values(foundation_path, allow_runtime_expiry=False)
    except (WorkspaceParameterError, FoundationParameterError) as error:
        raise WorkspacePreflightError(str(error)) from error
    if any(config[name] != values[name] for name in config if name not in {"schemaVersion"}):
        raise WorkspacePreflightError("workspace config and parameters differ")
    shared = (
        "location",
        "environment",
        "projectName",
        "sawResourceGroupName",
        "platformResourceGroupName",
        "ownerTag",
        "additionalTags",
    )
    if any(config[name] != foundation[name] for name in shared):
        raise WorkspacePreflightError("workspace and foundation contracts differ")
    if config["sawVnetName"] != f"vnet-{config['projectName']}-saw-{config['environment']}":
        raise WorkspacePreflightError("workspace VNet name differs from the foundation contract")
    current = now or dt.datetime.now(dt.UTC)
    expires = parse_utc(values["expiresAt"])
    if expires <= current + dt.timedelta(minutes=30) or expires > current + dt.timedelta(hours=4, minutes=5):
        raise WorkspacePreflightError("workspace lifetime must leave 30 minutes and not exceed four hours")
    expirations = (dependency_expiration(foundation_path),) + tuple(
        dependency_expiration(path) for path in dependency_paths
    )
    if any(expires > dependency for dependency in expirations):
        raise WorkspacePreflightError("workspace outlives a foundation or trusted-service dependency")
    return values


def capability(sku: dict[str, Any], name: str) -> str | None:
    return next(
        (str(item.get("value")) for item in sku.get("capabilities") or [] if item.get("name") == name),
        None,
    )


def usage_name(item: dict[str, Any]) -> str:
    name = item.get("name")
    if isinstance(name, dict):
        return str(name.get("value") or name.get("localizedValue") or "")
    return str(name or "")


def validate_azure(values: dict[str, Any], *, require_budget: bool) -> None:
    account = az_json(["account", "show"], timeout=20, category="workspace account")
    expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
    if account.get("environmentName") != "AzureCloud" or (
        expected_subscription and account.get("id") != expected_subscription
    ):
        raise WorkspacePreflightError("active Azure account or cloud differs")
    try:
        validate_budget(account, required=require_budget, maximum_amount=50)
    except PlatformPreflightError as error:
        raise WorkspacePreflightError(str(error)) from error
    providers = az_json(["provider", "list"], timeout=90, category="workspace provider")
    states = {item.get("namespace"): item.get("registrationState") for item in providers}
    if any(states.get(name) != "Registered" for name in REQUIRED_PROVIDERS):
        raise WorkspacePreflightError("workspace Azure providers are not registered")
    resources = az_json(
        ["resource", "list", "--resource-group", values["sawResourceGroupName"]],
        timeout=45,
        category="workspace inventory",
    )
    if {str(item.get("type", "")).casefold() for item in resources} != BASE_RESOURCE_TYPES:
        raise WorkspacePreflightError("SAW group is not in exact network-foundation state")
    vnet = az_json(
        [
            "network",
            "vnet",
            "show",
            "--resource-group",
            values["sawResourceGroupName"],
            "--name",
            values["sawVnetName"],
        ],
        timeout=30,
        category="workspace VNet",
    )
    subnets = [item for item in vnet.get("subnets") or [] if item.get("name") == values["workspaceSubnetName"]]
    if len(subnets) != 1 or subnets[0].get("natGateway") is not None:
        raise WorkspacePreflightError("SAW workspace subnet differs or has an unexpected NAT gateway")
    image = az_json(
        [
            "vm",
            "image",
            "show",
            "--location",
            values["location"],
            "--urn",
            (
                f"{values['imagePublisher']}:{values['imageOffer']}:"
                f"{values['imageSku']}:{values['imageVersion']}"
            ),
        ],
        timeout=60,
        category="workspace image",
    )
    if (
        image.get("architecture") != "x64"
        or image.get("hyperVGeneration") != "V2"
        or image.get("plan") is not None
    ):
        raise WorkspacePreflightError("workspace image architecture, generation, or plan differs")
    skus = az_json(
        [
            "vm",
            "list-skus",
            "--location",
            values["location"],
            "--resource-type",
            "virtualMachines",
            "--size",
            values["vmSize"],
        ],
        timeout=300,
        category="workspace SKU",
    )
    matching = [item for item in skus if item.get("name") == values["vmSize"]]
    if len(matching) != 1:
        raise WorkspacePreflightError("workspace VM SKU is unavailable")
    sku = matching[0]
    location_restrictions = [
        item for item in sku.get("restrictions") or [] if item.get("type") == "Location"
    ]
    if location_restrictions:
        raise WorkspacePreflightError("workspace VM SKU is restricted in the selected region")
    if (
        capability(sku, "vCPUs") != "2"
        or capability(sku, "MemoryGB") != "8"
        or "V2" not in str(capability(sku, "HyperVGenerations"))
    ):
        raise WorkspacePreflightError("workspace VM SKU capacity or generation differs")
    usage = az_json(
        ["vm", "list-usage", "--location", values["location"]],
        timeout=90,
        category="workspace quota",
    )
    family = [item for item in usage if usage_name(item).casefold() == "standarddsv5family"]
    if len(family) != 1 or int(family[0].get("currentValue", 0)) + 2 > int(family[0].get("limit", 0)):
        raise WorkspacePreflightError("workspace VM family quota is insufficient")
    print("PASS selected exact Ubuntu Gen2 image and D2s v5 quota are available")
    print("INFO nested virtualization remains fail-closed at the bootstrap /dev/kvm gate")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--foundation-parameters", type=Path, required=True)
    parser.add_argument("--dependency", type=Path, action="append", default=[])
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--require-budget", action="store_true")
    args = parser.parse_args()
    try:
        values = validate_local(
            args.config,
            args.parameters,
            args.foundation_parameters,
            tuple(args.dependency),
        )
        print("PASS workspace configuration, foundation, and dependency lifetimes agree")
        if args.offline:
            print("PASS offline workspace preflight complete")
            return 0
        if shutil.which("az") is None:
            raise WorkspacePreflightError("Azure CLI is not installed")
        validate_azure(values, require_budget=args.require_budget)
        print("PASS SAW network foundation is ready for one bounded workspace")
        print("INFO no resources were created or changed")
        return 0
    except WorkspacePreflightError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())