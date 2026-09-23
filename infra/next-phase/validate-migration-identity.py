#!/usr/bin/env python3
"""Validate the temporary cross-region SQL admin identity overlay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def validate(template: dict[str, object]) -> list[str]:
    failures: list[str] = []
    resources = template.get("resources") or []
    if not isinstance(resources, list) or len(resources) != 1:
        return ["migration identity overlay must contain exactly one resource"]
    identity = resources[0]
    if not isinstance(identity, dict) or identity.get("type") != "Microsoft.ManagedIdentity/userAssignedIdentities":
        return ["migration identity overlay resource type differs"]
    if (identity.get("properties") or {}).get("isolationScope") != "None":
        failures.append("migration identity overlay must use temporary cross-region scope")
    serialized = json.dumps(template, sort_keys=True).casefold()
    for required in ("database-bootstrap-admin", "temporarycrossregionuse", "migrationscopeexpiresat"):
        if required not in serialized:
            failures.append(f"migration identity overlay contract is missing: {required}")
    for prohibited in ("microsoft.app", "roleassignments", "password", "clientid", "principalid"):
        if prohibited in serialized:
            failures.append(f"migration identity overlay contains prohibited configuration: {prohibited}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read migration identity template: {error}", file=sys.stderr)
        return 1
    failures = validate(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS migration identity overlay changes one identity to bounded cross-region use")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())