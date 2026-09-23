#!/usr/bin/env python3
"""Validate temporary bootstrap egress and subnet association what-if output."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from summarize_database_what_if import change_resource_type


def validate(document: dict[str, Any], mode: str) -> list[str]:
    changes = document.get("changes")
    if not isinstance(changes, list) or any(not isinstance(item, dict) for item in changes):
        return ["workspace bootstrap egress what-if shape differs"]
    active_changes = [
        item for item in changes if str(item.get("changeType")) not in {"Ignore", "NoChange"}
    ]
    observed = Counter(
        (str(item.get("changeType")), change_resource_type(item))
        for item in active_changes
    )
    expected = (
        Counter(
            {
                ("Create", "Microsoft.Network/publicIPAddresses"): 1,
                ("Create", "Microsoft.Network/natGateways"): 1,
            }
        )
        if mode == "resources"
        else Counter({("Modify", "Microsoft.Network/virtualNetworks/subnets"): 1})
    )
    if observed != expected:
        return [f"workspace bootstrap egress {mode} what-if differs"]
    serialized = json.dumps(active_changes, sort_keys=True).casefold()
    if "microsoft.compute/" in serialized or "microsoft.authorization/roleassignments" in serialized:
        return ["workspace bootstrap egress what-if contains compute or RBAC"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("what_if", type=Path)
    parser.add_argument("--mode", choices=("resources", "association"), required=True)
    args = parser.parse_args()
    try:
        document = json.loads(args.what_if.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read workspace bootstrap egress what-if: {error}", file=sys.stderr)
        return 1
    failures = validate(document, args.mode)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print(f"PASS workspace bootstrap egress {args.mode} what-if is exact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())