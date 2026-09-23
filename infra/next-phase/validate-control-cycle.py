#!/usr/bin/env python3
"""Validate the compiled disposable live control-cycle stack."""

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
        failures.append("control-cycle stack contains an unapproved resource type")
    serialized = json.dumps(template, sort_keys=True).casefold()
    for required in (
        "one-shot-control-cycle",
        "wp3-control-cycle",
        "run-live-cycle-workflow.py",
        "-control-",
        "-diagnostic-",
        "@sha256:",
    ):
        if required not in serialized:
            failures.append(f"control-cycle evidence contract is missing: {required}")
    for prohibited in (
        "microsoft.authorization/roleassignments",
        "microsoft.sql/",
        "passwordsecretref",
        "systemassigned",
        "database-bootstrap-admin",
    ):
        if prohibited in serialized:
            failures.append(f"control-cycle stack contains prohibited configuration: {prohibited}")
    jobs = [item for item in resources if item.get("type") == "Microsoft.App/jobs"]
    if len(jobs) != 1:
        failures.append("control-cycle stack must contain exactly one job")
        return failures
    job = jobs[0]
    identity = job.get("identity") or {}
    if identity.get("type") != "UserAssigned" or len(identity.get("userAssignedIdentities") or {}) != 2:
        failures.append("control-cycle job must attach exactly two user-assigned identities")
    properties = job.get("properties") or {}
    configuration = properties.get("configuration") or {}
    manual = configuration.get("manualTriggerConfig") or {}
    if (
        properties.get("workloadProfileName") != "Consumption"
        or configuration.get("triggerType") != "Manual"
        or configuration.get("replicaTimeout") != 600
        or configuration.get("replicaRetryLimit") != 0
        or manual.get("parallelism") != 1
        or manual.get("replicaCompletionCount") != 1
    ):
        failures.append("control-cycle execution bounds differ")
    settings = configuration.get("identitySettings") or []
    if len(settings) != 1 or settings[0].get("lifecycle") != "Main":
        failures.append("control-cycle runtime identity setting differs")
    registries = configuration.get("registries") or []
    if (
        len(registries) != 1
        or "identity" not in registries[0]
        or "username" in registries[0]
        or "passwordSecretRef" in registries[0]
        or configuration.get("secrets") not in ([], None)
    ):
        failures.append("control-cycle registry or secret inventory differs")
    if "ingress" in configuration:
        failures.append("control-cycle job must not expose ingress")
    containers = ((properties.get("template") or {}).get("containers") or [])
    if len(containers) != 1:
        failures.append("control-cycle job must contain exactly one container")
        return failures
    container = containers[0]
    if container.get("command") != ["/usr/local/bin/python3"] or container.get("args") != [
        "/opt/learningnemo/scripts/run-live-cycle-workflow.py"
    ]:
        failures.append("control-cycle command differs")
    expected_environment = {
        "LEARNINGNEMO_WORKER_ENDPOINTS",
        "LEARNINGNEMO_WORKER_AUDIENCES",
        "LEARNINGNEMO_SQL_SERVER",
        "LEARNINGNEMO_SQL_DATABASE",
        "AZURE_CLIENT_ID",
        "LEARNINGNEMO_APPROVER_HASH",
        "LEARNINGNEMO_OPERATOR_HASH",
    }
    environment = {item.get("name") for item in container.get("env") or []}
    if environment != expected_environment:
        failures.append("control-cycle environment contract differs")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read compiled control-cycle stack: {error}", file=sys.stderr)
        return 1
    failures = validate(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS control-cycle job uses control runtime identity and diagnostic registry identity")
    print("PASS control-cycle job is manual, zero-retry, secret-free, bounded, and has no ingress")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())