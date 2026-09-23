#!/usr/bin/env python3
"""Require one run-command create after the private SAW VM is ready."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from summarize_database_what_if import change_resource_type


def validate(document: dict[str, Any], *, existing_resource_id: str | None = None) -> list[str]:
    changes = document.get("changes")
    if not isinstance(changes, list) or any(not isinstance(item, dict) for item in changes):
        return ["workspace bootstrap what-if shape differs"]
    active_changes = [
        item
        for item in changes
        if str(item.get("changeType")) not in {"Ignore", "NoChange"}
    ]
    observed = Counter(
        (str(item.get("changeType")), change_resource_type(item))
        for item in active_changes
    )
    operation = "Modify" if existing_resource_id else "Create"
    expected = Counter({(operation, "Microsoft.Compute/virtualMachines/runCommands"): 1})
    if observed != expected:
        return [f"workspace bootstrap what-if must {operation.lower()} only one VM run command"]
    if existing_resource_id and active_changes[0].get("resourceId") != existing_resource_id:
        return ["workspace bootstrap retry targets a different run command"]
    serialized = json.dumps(active_changes, sort_keys=True).casefold()
    for prohibited in (
        "microsoft.network/publicipaddresses",
        "microsoft.authorization/roleassignments",
        '"changetype": "delete"',
    ):
        if prohibited in serialized:
            return [f"workspace bootstrap what-if contains prohibited change: {prohibited}"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("what_if", type=Path)
    parser.add_argument("--existing-resource-id")
    args = parser.parse_args()
    try:
        document = json.loads(args.what_if.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read workspace bootstrap what-if: {error}", file=sys.stderr)
        return 1
    failures = validate(document, existing_resource_id=args.existing_resource_id)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS workspace bootstrap what-if changes only the bounded OpenShell run command")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())