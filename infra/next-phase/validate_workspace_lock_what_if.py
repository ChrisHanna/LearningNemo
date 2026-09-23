#!/usr/bin/env python3
"""Require exactly four post-bootstrap NSG rule creates."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from summarize_database_what_if import change_resource_type


def validate(document: dict[str, Any]) -> list[str]:
    changes = document.get("changes")
    if not isinstance(changes, list) or any(not isinstance(item, dict) for item in changes):
        return ["workspace lock what-if shape differs"]
    active_changes = [
        item for item in changes
        if str(item.get("changeType")) not in {"Ignore", "NoChange"}
    ]
    observed = Counter(
        (str(item.get("changeType")), change_resource_type(item))
        for item in active_changes
    )
    expected = Counter({("Create", "Microsoft.Network/networkSecurityGroups/securityRules"): 4})
    if observed != expected:
        return ["workspace lock what-if must contain exactly four security-rule creates"]
    serialized = json.dumps(active_changes, sort_keys=True).casefold()
    if "microsoft.compute/" in serialized or "microsoft.authorization/roleassignments" in serialized:
        return ["workspace lock what-if contains compute or RBAC changes"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("what_if", type=Path)
    args = parser.parse_args()
    try:
        document = json.loads(args.what_if.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read workspace lock what-if: {error}", file=sys.stderr)
        return 1
    failures = validate(document)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS workspace lock what-if contains exactly four post-bootstrap NSG rule creates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())