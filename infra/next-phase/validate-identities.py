#!/usr/bin/env python3
"""Fail closed unless the identity template contains exactly six regional identities."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from platform_contract import IDENTITY_PURPOSES


ALLOWED_RESOURCE_TYPES = {
    "Microsoft.ManagedIdentity/userAssignedIdentities",
    "Microsoft.Resources/deployments",
}


def resources_in(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        resources = value.get("resources")
        if isinstance(resources, list):
            found.extend(item for item in resources if isinstance(item, dict))
        for child in value.values():
            found.extend(resources_in(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(resources_in(child))
    return found


def validate_template(template: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    expected_schema = "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#"
    if template.get("$schema") != expected_schema:
        failures.append("compiled identities template uses an unexpected resource-group schema")
    if template.get("contentVersion") != "1.0.0.0":
        failures.append("compiled identities template contentVersion differs")
    resources = resources_in(template)
    resource_types = {str(resource.get("type", "")) for resource in resources}
    disallowed = sorted(resource_types - ALLOWED_RESOURCE_TYPES)
    if disallowed:
        failures.append(f"unapproved identity-stack resource types: {', '.join(disallowed)}")
    identities = [
        resource
        for resource in resources
        if resource.get("type") == "Microsoft.ManagedIdentity/userAssignedIdentities"
    ]
    if len(identities) != len(IDENTITY_PURPOSES):
        failures.append(f"expected six service identities, found {len(identities)}")
    if any((identity.get("properties") or {}).get("isolationScope") != "Regional" for identity in identities):
        failures.append("every service identity must use Regional isolationScope")
    serialized_identities = json.dumps(identities, sort_keys=True).casefold()
    for suffix, purpose in IDENTITY_PURPOSES.items():
        if suffix.casefold() not in serialized_identities or purpose.casefold() not in serialized_identities:
            failures.append(f"identity name or purpose is missing: {suffix}")
    serialized_template = json.dumps(template, sort_keys=True).casefold()
    for required_tag_value in (
        "identity-only-no-compute",
        "wp2a-identities",
        "plannedruntimeenvironment",
        "monthlycostceiling",
    ):
        if required_tag_value not in serialized_template:
            failures.append(f"identity tag contract is missing: {required_tag_value}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read compiled identities template: {error}", file=sys.stderr)
        return 1
    failures = validate_template(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS identity template contains exactly six regional service identities")
    print("PASS identity template contains no app, compute, network, database, or logging resource")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())