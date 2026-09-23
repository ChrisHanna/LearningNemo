#!/usr/bin/env python3
"""Require the exact bounded ARM change set for one SAW workspace."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from summarize_database_what_if import change_resource_type


EXPECTED_CREATES = Counter(
    {
        "Microsoft.Compute/virtualMachines": 1,
        "Microsoft.ManagedIdentity/userAssignedIdentities": 1,
        "Microsoft.Network/networkInterfaces": 1,
    }
)


def validate(document: dict[str, Any]) -> list[str]:
    changes = document.get("changes")
    if not isinstance(changes, list) or any(not isinstance(item, dict) for item in changes):
        return ["workspace what-if shape differs"]
    creates = Counter(
        change_resource_type(item)
        for item in changes
        if str(item.get("changeType")) == "Create"
    )
    other = [
        item
        for item in changes
        if str(item.get("changeType")) not in {"Create", "Ignore", "NoChange"}
    ]
    if creates != EXPECTED_CREATES or other:
        return ["workspace what-if differs from four bounded creates and existing-resource ignores"]
    serialized = json.dumps(document, sort_keys=True).casefold()
    for prohibited in (
        "microsoft.network/publicipaddresses",
        "microsoft.authorization/roleassignments",
        '"changetype": "delete"',
        '"changetype": "modify"',
        "adminpassword",
    ):
        if prohibited in serialized:
            return [f"workspace what-if contains prohibited change: {prohibited}"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("what_if", type=Path)
    args = parser.parse_args()
    try:
        document = json.loads(args.what_if.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read workspace what-if: {error}", file=sys.stderr)
        return 1
    failures = validate(document)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS workspace what-if contains only VM, NIC, and no-RBAC identity creates")
    print("PASS workspace what-if contains no public IP, RBAC, modify, or delete operation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())