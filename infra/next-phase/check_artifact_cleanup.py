#!/usr/bin/env python3
"""Fail closed unless the exact registry is owned and unused by LearningNeMo compute."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from artifact_contract import classify
from artifact_contract import tags
from artifact_contract import tags_match
from artifact_parameters import ArtifactParameterError
from artifact_parameters import load_config
from artifact_parameters import parameter_values


class ArtifactCleanupError(RuntimeError):
    pass


def az_json(arguments: list[str]) -> Any:
    result = subprocess.run(
        ["az", *arguments, "--output", "json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode != 0:
        raise ArtifactCleanupError("artifact cleanup readiness query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise ArtifactCleanupError("artifact cleanup returned unreadable JSON") from error


def load_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArtifactCleanupError("unable to read owner-only artifact state") from error
    if not isinstance(state, dict) or set(state) != {
        "schemaVersion",
        "resourceGroupName",
        "registryName",
        "loginServer",
        "expiresAt",
    } or state.get("schemaVersion") != 1:
        raise ArtifactCleanupError("owner-only artifact state fields differ")
    return state


def image_references(resources: list[dict[str, Any]]) -> set[str]:
    references: set[str] = set()
    for resource in resources:
        containers = (((resource.get("properties") or {}).get("template") or {}).get("containers") or [])
        references.update(
            str(container.get("image", "")).casefold()
            for container in containers
            if isinstance(container, dict)
        )
    return references


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--parameters", type=Path)
    parser.add_argument("--private-state", type=Path)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        group_name = config["artifactResourceGroupName"]
        if az_json(["group", "exists", "--name", group_name]) is not True:
            print("PASS artifact resource group is already absent")
            return 0
        if args.parameters is None or args.private_state is None:
            raise ArtifactCleanupError("owner-only artifact parameters and state are required")
        values = parameter_values(args.parameters)
        state = load_state(args.private_state)
        if state["resourceGroupName"] != group_name or state["expiresAt"] != values["expiresAt"]:
            raise ArtifactCleanupError("artifact state belongs to another deployment")
        group = az_json(["group", "show", "--name", group_name])
        if group.get("location") != values["location"] or not tags_match(group.get("tags"), tags(values)):
            raise ArtifactCleanupError("artifact group ownership, expiry, or location differs")
        resources = az_json(["resource", "list", "--resource-group", group_name])
        if classify(resources) != "complete" or resources[0].get("name") != state["registryName"]:
            raise ArtifactCleanupError("artifact registry inventory differs")
        if not tags_match(resources[0].get("tags"), tags(values)):
            raise ArtifactCleanupError("artifact registry tags differ")
        compute = az_json(
            ["resource", "list", "--resource-type", "Microsoft.App/containerApps"],
        ) + az_json(
            ["resource", "list", "--resource-type", "Microsoft.App/jobs"],
        )
        prefix = f"{state['loginServer']}/".casefold()
        if any(reference.startswith(prefix) for reference in image_references(compute)):
            raise ArtifactCleanupError("a Container Apps workload still references the registry")
        print("PASS exact owned artifact registry is unused and eligible for removal")
        return 0
    except (ArtifactCleanupError, ArtifactParameterError) as error:
        print(f"FAIL delete blocked: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())