#!/usr/bin/env python3
"""Print an identifier-safe summary of Azure what-if JSON."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def safe_resource_name(resource_id: str) -> str:
    parts = resource_id.strip("/").split("/")
    if "providers" in parts:
        provider_index = parts.index("providers")
        resource_parts = parts[provider_index + 1 :]
        if len(resource_parts) >= 3:
            namespace = resource_parts[0]
            types = resource_parts[1::2]
            names = resource_parts[2::2]
            return f"{namespace}/{'/'.join(types)}/{'/'.join(names)}"
    if "resourceGroups" in parts:
        index = parts.index("resourceGroups")
        if len(parts) > index + 1:
            return f"Microsoft.Resources/resourceGroups/{parts[index + 1]}"
    return "unresolved-resource"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("what_if", type=Path)
    args = parser.parse_args()
    try:
        document = json.loads(args.what_if.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read Azure what-if result: {error}", file=sys.stderr)
        return 1
    changes = document.get("changes") or []
    counts = Counter(str(change.get("changeType", "Unknown")) for change in changes)
    print("What-if resource changes:")
    for change in changes:
        print(f"  {change.get('changeType', 'Unknown')}: {safe_resource_name(str(change.get('resourceId', '')))}")
    print("What-if totals: " + ", ".join(f"{name}={counts[name]}" for name in sorted(counts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())