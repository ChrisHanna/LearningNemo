#!/usr/bin/env python3
"""Validate the isolated one-resource OpenShell bootstrap template."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from workspace_parameters import BOOTSTRAP_PARAMETERS


def validate(template: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    parameters = template.get("parameters")
    if not isinstance(parameters, dict) or set(parameters) != BOOTSTRAP_PARAMETERS:
        failures.append("OpenShell bootstrap parameter inventory differs")
    resources = [item for item in template.get("resources", []) if isinstance(item, dict)]
    commands = [
        item for item in resources
        if item.get("type") == "Microsoft.Compute/virtualMachines/runCommands"
    ]
    actual_types = {str(item.get("type", "")) for item in resources}
    if len(commands) != 1 or actual_types != {"Microsoft.Compute/virtualMachines/runCommands"}:
        failures.append("OpenShell bootstrap must contain exactly one run command")
        return failures
    properties = commands[0].get("properties") or {}
    if (
        properties.get("asyncExecution") is not False
        or properties.get("treatFailureAsDeploymentFailure") is not True
        or properties.get("timeoutInSeconds") != 2700
        or not isinstance((properties.get("source") or {}).get("script"), str)
    ):
        failures.append("OpenShell bootstrap execution bounds or source differ")
    serialized = json.dumps(template, sort_keys=True).casefold()
    for required in (
        "bootstrap-openshell",
        "openshellpackageurl",
        "openshellpackagesha256",
        "sandboximagereference",
        "supervisorimagereference",
        "sqlserverhostname",
        "planning-policy.yaml",
        "execution-policy.yaml",
        "probe-policy.yaml",
    ):
        if required not in serialized:
            failures.append(f"OpenShell bootstrap contract is missing: {required}")
    for prohibited in (
        "microsoft.network/networkinterfaces",
        "microsoft.network/publicipaddresses",
        "microsoft.managedidentity/userassignedidentities",
        "microsoft.authorization/roleassignments",
        "adminpassword",
    ):
        if prohibited in serialized:
            failures.append(f"OpenShell bootstrap contains prohibited configuration: {prohibited}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read compiled OpenShell bootstrap: {error}", file=sys.stderr)
        return 1
    failures = validate(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS OpenShell bootstrap owns exactly one bounded VM run command")
    print("PASS OpenShell bootstrap contains pinned package, images, and three policies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())