#!/usr/bin/env python3
"""Validate the compiled expiring Basic ACR and least-authority pull bindings."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


EXPECTED_TYPES = Counter(
    {
        "Microsoft.ContainerRegistry/registries": 1,
        "Microsoft.Authorization/roleAssignments": 5,
    }
)
ACR_PULL_ROLE = "7f951dda-4ed3-4680-a7ca-43fe172d538d"


def resource_values(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    return []


def nested_resources(template: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for resource in resource_values(template.get("resources")):
        nested = ((resource.get("properties") or {}).get("template") or {}).get("resources")
        result.extend(resource_values(nested))
    return result


def validate(template: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    roots = Counter(str(item.get("type", "")) for item in resource_values(template.get("resources")))
    if roots != Counter({"Microsoft.Resources/resourceGroups": 1, "Microsoft.Resources/deployments": 1}):
        failures.append("artifact subscription inventory differs")
    resources = nested_resources(template)
    types = Counter(str(item.get("type", "")) for item in resources)
    if types != EXPECTED_TYPES:
        failures.append("artifact registry inventory differs")
        return failures
    registry = next(item for item in resources if item.get("type") == "Microsoft.ContainerRegistry/registries")
    properties = registry.get("properties") or {}
    if (
        (registry.get("sku") or {}).get("name") != "Basic"
        or properties.get("adminUserEnabled") is not False
        or properties.get("anonymousPullEnabled") is not False
        or properties.get("dataEndpointEnabled") is not False
        or properties.get("publicNetworkAccess") != "Enabled"
    ):
        failures.append("artifact registry access or cost controls differ")
    assignments = [item for item in resources if item.get("type") == "Microsoft.Authorization/roleAssignments"]
    if any(
        not any(
            marker in str((item.get("properties") or {}).get("roleDefinitionId", "")).casefold()
            for marker in (ACR_PULL_ROLE, "acrpullroledefinitionid")
        )
        or (item.get("properties") or {}).get("principalType") != "ServicePrincipal"
        for item in assignments
    ):
        failures.append("artifact pull role bindings differ")
    serialized = json.dumps(template, sort_keys=True).casefold()
    for required in ("expiring-basic-container-registry", "wp3-artifacts", "expiresat", "acrpull"):
        if required not in serialized:
            failures.append(f"artifact registry contract is missing: {required}")
    for prohibited in ("adminenabled", "password", "microsoft.app", "microsoft.compute", "premium"):
        if prohibited in serialized:
            failures.append(f"artifact registry contains prohibited configuration: {prohibited}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read compiled artifact template: {error}", file=sys.stderr)
        return 1
    failures = validate(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS expiring Basic ACR disables admin and anonymous access")
    print("PASS exactly five managed identities receive only AcrPull")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())