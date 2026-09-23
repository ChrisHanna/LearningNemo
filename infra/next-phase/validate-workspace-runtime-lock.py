#!/usr/bin/env python3
"""Validate the post-bootstrap SAW runtime egress lock."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


EXPECTED = {
    (121, "Deny", "AzurePlatformIMDS", "*"),
    (122, "Allow", "168.63.129.16/32", "53"),
    (125, "Allow", "AzureCloud", "443"),
    (130, "Deny", "Internet", "*"),
}


def validate(template: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    rules = [
        item
        for item in template.get("resources", [])
        if isinstance(item, dict)
        and item.get("type") == "Microsoft.Network/networkSecurityGroups/securityRules"
    ]
    observed: set[tuple[Any, Any, Any, Any]] = set()
    for rule in rules:
        name = str(rule.get("name", "")).rsplit("/", 1)[-1]
        properties = rule.get("properties") or {}
        observed.add((
            properties.get("priority"),
            properties.get("access"),
            properties.get("destinationAddressPrefix"),
            properties.get("destinationPortRange"),
        ))
        if properties.get("direction") != "Outbound" or properties.get("sourceAddressPrefix") != "*":
            failures.append(f"workspace runtime rule direction differs: {name}")
    if observed != EXPECTED:
        failures.append("workspace runtime lock rule contract differs")
    serialized = json.dumps(template, sort_keys=True).casefold()
    for prohibited in (
        "microsoft.network/publicipaddresses",
        "microsoft.authorization/roleassignments",
        "microsoft.compute/",
        "inbound",
        "sql",
    ):
        if prohibited in serialized:
            failures.append(f"workspace runtime lock contains prohibited configuration: {prohibited}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read runtime lock template: {error}", file=sys.stderr)
        return 1
    failures = validate(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS runtime lock removes general Internet and IMDS after bootstrap")
    print("PASS runtime lock retains only Azure DNS and Azure HTTPS before OpenShell policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())