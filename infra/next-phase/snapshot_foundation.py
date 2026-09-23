#!/usr/bin/env python3
"""Capture a non-secret WP1 resource inventory before guarded deletion."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


SAFE_TAG_NAMES = {
    "project",
    "environment",
    "managedBy",
    "owner",
    "costProfile",
    "trustZone",
    "disposable",
    "expiresOn",
    "sawMaturity",
    "platformPhase",
}


class SnapshotError(RuntimeError):
    pass


def az_json(arguments: list[str]) -> Any:
    try:
        result = subprocess.run(
            ["az", *arguments, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SnapshotError("Azure inventory query failed or timed out") from error
    if result.returncode != 0:
        raise SnapshotError("Azure inventory query failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SnapshotError("Azure inventory query returned unreadable JSON") from error


def build_snapshot(group: dict[str, Any], resources: list[dict[str, Any]]) -> dict[str, Any]:
    tags = group.get("tags") or {}
    safe_tags = {name: tags[name] for name in sorted(SAFE_TAG_NAMES) if name in tags}
    resource_inventory = sorted(
        (
            {
                "name": str(resource.get("name", "")),
                "type": str(resource.get("type", "")),
                "location": str(resource.get("location", "")),
            }
            for resource in resources
        ),
        key=lambda item: (item["type"].casefold(), item["name"].casefold()),
    )
    return {
        "schemaVersion": 1,
        "capturedAt": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
        "resourceGroup": {
            "name": str(group.get("name", "")),
            "location": str(group.get("location", "")),
            "tags": safe_tags,
        },
        "resources": resource_inventory,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        group = az_json(["group", "show", "--name", args.resource_group])
        resources = az_json(["resource", "list", "--resource-group", args.resource_group])
        snapshot = build_snapshot(group, resources)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(args.output)
        print(f"PASS wrote pre-delete inventory to {args.output}")
        return 0
    except SnapshotError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())