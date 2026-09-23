#!/usr/bin/env python3
"""Validate reversible SAW subnet bootstrap egress association."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def validate(template: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    resources = [item for item in template.get("resources", []) if isinstance(item, dict)]
    subnets = [item for item in resources if item.get("type") == "Microsoft.Network/virtualNetworks/subnets"]
    if len(subnets) != 1:
        return ["subnet egress template must contain exactly one managed subnet resource"]
    properties = subnets[0].get("properties") or {}
    if (
        "natGateway" not in properties
        or "networkSecurityGroup" not in properties
        or "routeTable" not in properties
        or properties.get("privateEndpointNetworkPolicies") != "Enabled"
        or properties.get("privateLinkServiceNetworkPolicies") != "Enabled"
    ):
        failures.append("subnet egress association does not preserve the foundation contract")
    serialized = json.dumps(template, sort_keys=True).casefold()
    for required in ("attachbootstrapegress", "snet-workspace", "natgateways", "networksecuritygroups", "routetables"):
        if required not in serialized:
            failures.append(f"subnet egress contract is missing: {required}")
    for prohibited in ("publicipaddresses", "microsoft.compute", "roleassignments"):
        if prohibited in serialized:
            failures.append(f"subnet egress association contains prohibited configuration: {prohibited}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read subnet egress template: {error}", file=sys.stderr)
        return 1
    failures = validate(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS subnet egress association is reversible and preserves NSG, route, and policies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())