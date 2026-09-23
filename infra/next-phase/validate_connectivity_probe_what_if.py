#!/usr/bin/env python3
"""Require the exact bounded change set for the connectivity probe."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from summarize_database_what_if import change_resource_type


EXPECTED_CHANGES = Counter(
    {
        ("Create", "Microsoft.App/jobs"): 1,
        ("Create", "Microsoft.Resources/resourceGroups"): 1,
    }
)


def validate(document: dict[str, Any]) -> list[str]:
    changes = document.get("changes")
    if not isinstance(changes, list) or any(not isinstance(change, dict) for change in changes):
        return ["connectivity probe what-if shape differs"]
    observed = Counter(
        (str(change.get("changeType", "Unknown")), change_resource_type(change))
        for change in changes
    )
    if observed != EXPECTED_CHANGES:
        return ["connectivity probe what-if differs from the disposable group and job creates"]
    serialized = json.dumps(document, sort_keys=True).casefold()
    if "microsoft.sql/" in serialized or '"changetype": "delete"' in serialized:
        return ["connectivity probe what-if contains SQL or delete operations"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("what_if", type=Path)
    args = parser.parse_args()
    try:
        document = json.loads(args.what_if.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read connectivity probe what-if: {error}", file=sys.stderr)
        return 1
    failures = validate(document)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS connectivity probe what-if contains only the disposable group and job creates")
    print("PASS connectivity probe what-if contains no SQL, modify, or delete operations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())