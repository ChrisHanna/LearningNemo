#!/usr/bin/env python3
"""Validate temporary SAW bootstrap NAT resources."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def validate(template: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    resources = [item for item in template.get("resources", []) if isinstance(item, dict)]
    types = [str(item.get("type", "")) for item in resources]
    if types.count("Microsoft.Network/publicIPAddresses") != 1 or types.count("Microsoft.Network/natGateways") != 1 or len(resources) != 2:
        failures.append("bootstrap egress must contain exactly one public IP and one NAT gateway")
        return failures
    public_ip = next(item for item in resources if item.get("type") == "Microsoft.Network/publicIPAddresses")
    nat = next(item for item in resources if item.get("type") == "Microsoft.Network/natGateways")
    public_properties = public_ip.get("properties") or {}
    if (
        (public_ip.get("sku") or {}).get("name") != "Standard"
        or public_properties.get("publicIPAllocationMethod") != "Static"
        or public_properties.get("publicIPAddressVersion") != "IPv4"
        or public_properties.get("idleTimeoutInMinutes") != 4
    ):
        failures.append("bootstrap public IP configuration differs")
    nat_properties = nat.get("properties") or {}
    if (
        (nat.get("sku") or {}).get("name") != "Standard"
        or nat_properties.get("idleTimeoutInMinutes") != 4
        or len(nat_properties.get("publicIpAddresses") or []) != 1
    ):
        failures.append("bootstrap NAT configuration differs")
    serialized = json.dumps(template, sort_keys=True).casefold()
    for required in ("temporary-saw-bootstrap-egress", "wp5-saw-bootstrap", "expiresat"):
        if required not in serialized:
            failures.append(f"bootstrap egress contract is missing: {required}")
    for prohibited in ("microsoft.compute", "roleassignments", "inboundnat", "publicipallocationmethod\": \"dynamic"):
        if prohibited in serialized:
            failures.append(f"bootstrap egress contains prohibited configuration: {prohibited}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read bootstrap egress template: {error}", file=sys.stderr)
        return 1
    failures = validate(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS bootstrap egress is one temporary Standard NAT and public IP")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())