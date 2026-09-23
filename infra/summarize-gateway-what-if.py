#!/usr/bin/env python3
"""Print change counts from gateway what-if JSON without resource identifiers."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


KNOWN_RESOURCE_TYPES = (
    "Microsoft.ApiManagement/service/apis/operations/policies",
    "Microsoft.ApiManagement/service/apis/operations",
    "Microsoft.ApiManagement/service/apis/policies",
    "Microsoft.ApiManagement/service/namedValues",
    "Microsoft.ApiManagement/service/apis",
    "Microsoft.ApiManagement/service",
    "Microsoft.Authorization/roleAssignments",
    "Microsoft.KeyVault/vaults",
    "Microsoft.Resources/resourceGroups",
)


def resource_type(resource_id: str) -> str:
    normalized = resource_id.casefold()
    if "roleassignments" in normalized:
        return "Microsoft.Authorization/roleAssignments"
    parts = resource_id.strip("/").split("/")
    provider_indexes = [
        index for index, part in enumerate(parts) if part.casefold() == "providers"
    ]
    if provider_indexes:
        resource_parts = parts[provider_indexes[-1] + 1 :]
        if len(resource_parts) >= 2:
            candidate = f"{resource_parts[0]}/{'/'.join(resource_parts[1::2])}"
            canonical = next(
                (
                    known_type
                    for known_type in KNOWN_RESOURCE_TYPES
                    if known_type.casefold() == candidate.casefold()
                ),
                None,
            )
            if canonical is not None:
                return canonical
    for known_type in KNOWN_RESOURCE_TYPES:
        if known_type.casefold() in normalized:
            return known_type
    if "resourcegroups" in normalized:
        return "Microsoft.Resources/resourceGroups"
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("what_if", type=Path)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    try:
        document = json.loads(args.what_if.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read {args.label} what-if result: {error}", file=sys.stderr)
        return 1
    changes = document.get("changes") or []
    counts = Counter(str(change.get("changeType", "Unknown")) for change in changes)
    summary = ", ".join(f"{name}={counts[name]}" for name in sorted(counts)) or "none=0"
    print(f"{args.label.capitalize()} what-if totals: {summary}")
    by_type = Counter(
        (str(change.get("changeType", "Unknown")), resource_type(str(change.get("resourceId", ""))))
        for change in changes
    )
    for (change_type, resource_name), count in sorted(by_type.items()):
        print(f"  {change_type} {resource_name}={count}")
    if counts.get("Delete", 0):
        print(f"FAIL {args.label} what-if contains delete operations", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())