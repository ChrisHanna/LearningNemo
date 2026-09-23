#!/usr/bin/env python3
"""Validate the compiled one-shot Azure SQL migration job."""

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
    types = {str(item.get("type", "")) for item in resources}
    allowed = {
        "Microsoft.Resources/resourceGroups",
        "Microsoft.Resources/deployments",
        "Microsoft.App/jobs",
    }
    if types - allowed:
        failures.append("migration template contains an unapproved resource type")
    jobs = [item for item in resources if item.get("type") == "Microsoft.App/jobs"]
    if len(jobs) != 1:
        failures.append("migration template must contain exactly one job")
        return failures
    job = jobs[0]
    identity = job.get("identity") or {}
    if identity.get("type") != "UserAssigned" or len(identity.get("userAssignedIdentities") or {}) != 1:
        failures.append("migration job must use exactly one managed identity")
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
        failures.append("migration job execution bounds differ")
    if len(configuration.get("registries") or []) != 1 or len(configuration.get("secrets") or []) != 1:
        failures.append("migration job registry or secret inventory differs")
    registry = (configuration.get("registries") or [{}])[0]
    if "identity" not in registry or "username" in registry or "passwordSecretRef" in registry:
        failures.append("migration registry authentication must use managed identity")
    containers = ((properties.get("template") or {}).get("containers") or [])
    if len(containers) != 1:
        failures.append("migration job must contain exactly one container")
        return failures
    container = containers[0]
    if container.get("command") != ["python3"] or container.get("args") != [
        "/opt/learningnemo/scripts/apply-sql-migrations.py"
    ]:
        failures.append("migration job command differs")
    environment = {item.get("name"): item.get("value") for item in container.get("env") or []}
    if set(environment) != {
        "LEARNINGNEMO_SQL_MIGRATION_BUNDLE",
        "LEARNINGNEMO_SQL_MIGRATION_SHA256",
        "LEARNINGNEMO_SQL_SERVER",
        "LEARNINGNEMO_SQL_DATABASE",
        "AZURE_CLIENT_ID",
    }:
        failures.append("migration job environment differs")
    template_spec = properties.get("template") or {}
    volumes = template_spec.get("volumes") or []
    mounts = container.get("volumeMounts") or []
    if (
        len(volumes) != 1
        or volumes[0].get("storageType") != "Secret"
        or len(volumes[0].get("secrets") or []) != 1
        or len(mounts) != 1
        or mounts[0].get("mountPath") != "/var/run/learningnemo"
    ):
        failures.append("migration bundle secret mount differs")
    serialized = json.dumps(template, sort_keys=True).casefold()
    for required in (
        "one-shot-consumption-job",
        "wp3-database-migration",
        "migrationbundlehash",
        "@sha256:",
    ):
        if required not in serialized:
            failures.append(f"migration evidence contract is missing: {required}")
    for prohibited in ("external", "ingress", "passwordsecretref", "systemassigned", "replicaretrylimit\":1"):
        if prohibited in serialized:
            failures.append(f"migration template contains prohibited configuration: {prohibited}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read compiled migration template: {error}", file=sys.stderr)
        return 1
    failures = validate(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS one-shot migration job uses a digest, SQL-admin identity, and no ingress")
    print("PASS migration execution is single-replica, zero-retry, bounded, and hash-bound")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())