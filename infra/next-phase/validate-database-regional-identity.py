#!/usr/bin/env python3
"""Validate the least-authority SQL admin identity restoration template."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def validate(template: dict[str, object]) -> list[str]:
    resources = template.get("resources") or []
    if not isinstance(resources, list) or len(resources) != 1:
        return ["regional identity restore must contain exactly one resource"]
    identity = resources[0]
    if not isinstance(identity, dict) or identity.get("type") != "Microsoft.ManagedIdentity/userAssignedIdentities":
        return ["regional identity restore resource type differs"]
    failures: list[str] = []
    if (identity.get("properties") or {}).get("isolationScope") != "Regional":
        failures.append("SQL admin identity restore must use regional isolation")
    serialized = json.dumps(template, sort_keys=True).casefold()
    for required in ("database-bootstrap-admin", "freelimit", "wp3-database"):
        if required not in serialized:
            failures.append(f"regional identity restore contract is missing: {required}")
    for prohibited in (
        "microsoft.app",
        "roleassignments",
        "password",
        "clientid",
        "principalid",
        "temporarycrossregionuse",
        "migrationscopeexpiresat",
    ):
        if prohibited in serialized:
            failures.append(f"regional identity restore contains prohibited configuration: {prohibited}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read regional identity restore template: {error}", file=sys.stderr)
        return 1
    failures = validate(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS SQL admin identity restore is regional and least-authority")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())