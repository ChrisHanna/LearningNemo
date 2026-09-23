#!/usr/bin/env python3
"""Validate the compiled least-authority SQL connectivity probe stack."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def resource_values(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    return []


def all_resources(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.extend(resource_values(value.get("resources")))
        for child in value.values():
            found.extend(all_resources(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(all_resources(child))
    return found


def validate(template: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    resources = all_resources(template)
    allowed = {
        "Microsoft.App/jobs",
        "Microsoft.Resources/deployments",
        "Microsoft.Resources/resourceGroups",
    }
    types = {str(item.get("type", "")) for item in resources}
    if types - allowed:
        failures.append("connectivity probe contains an unapproved resource type")
    serialized = json.dumps(template, sort_keys=True).casefold()
    for required in (
        "one-shot-connectivity-probe",
        "wp3-connectivity-probe",
        "connectivity-probe",
        "@sha256:",
    ):
        if required not in serialized:
            failures.append(f"connectivity probe evidence contract is missing: {required}")
    for prohibited in (
        "microsoft.sql/",
        "database-bootstrap-admin",
        "sql-admin",
        "identitysettings",
        "ingress",
        "systemassigned",
        "passwordsecretref",
    ):
        if prohibited in serialized:
            failures.append(f"connectivity probe contains prohibited configuration: {prohibited}")
    jobs = [item for item in resources if item.get("type") == "Microsoft.App/jobs"]
    assignments = [item for item in resources if item.get("type") == "Microsoft.Authorization/roleAssignments"]
    if assignments:
        failures.append("connectivity probe must not create role assignments")
    if "id-{0}-diagnostic-{1}" not in serialized and "-diagnostic-" not in serialized:
        failures.append("connectivity probe must reuse the diagnostic identity")
    if len(jobs) != 1:
        failures.append("connectivity probe must contain exactly one job")
        return failures
    job = jobs[0]
    identity = job.get("identity") or {}
    if identity.get("type") != "UserAssigned" or len(identity.get("userAssignedIdentities") or {}) != 1:
        failures.append("connectivity probe job identity differs")
    properties = job.get("properties") or {}
    configuration = properties.get("configuration") or {}
    manual = configuration.get("manualTriggerConfig") or {}
    if (
        properties.get("workloadProfileName") != "Consumption"
        or configuration.get("triggerType") != "Manual"
        or configuration.get("replicaTimeout") != 180
        or configuration.get("replicaRetryLimit") != 0
        or manual.get("parallelism") != 1
        or manual.get("replicaCompletionCount") != 1
    ):
        failures.append("connectivity probe execution bounds differ")
    if len(configuration.get("registries") or []) != 1 or configuration.get("secrets") not in ([], None):
        failures.append("connectivity probe registry or secret inventory differs")
    registry = (configuration.get("registries") or [{}])[0]
    if "identity" not in registry or "username" in registry or "passwordSecretRef" in registry:
        failures.append("connectivity probe registry must use managed identity")
    containers = ((properties.get("template") or {}).get("containers") or [])
    if len(containers) != 1:
        failures.append("connectivity probe must contain one container")
        return failures
    container = containers[0]
    if container.get("command") != ["/usr/local/bin/python3"] or container.get("args") != [
        "/opt/learningnemo/scripts/probe-sql-odbc.py"
    ]:
        failures.append("connectivity probe command differs")
    environment = {item.get("name"): item.get("value") for item in container.get("env") or []}
    if set(environment) != {"LEARNINGNEMO_SQL_SERVER", "LEARNINGNEMO_SQL_DATABASE", "AZURE_CLIENT_ID"}:
        failures.append("connectivity probe environment differs")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read compiled connectivity probe template: {error}", file=sys.stderr)
        return 1
    failures = validate(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS connectivity probe reuses the existing diagnostic identity with no RBAC changes")
    print("PASS connectivity probe is manual, zero-retry, secret-free, and has no ingress")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())