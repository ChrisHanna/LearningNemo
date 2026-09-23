#!/usr/bin/env python3
"""Validate the compiled subscription budget template."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


EXPECTED_NOTIFICATIONS = {
    "actual50": (50, "Actual"),
    "actual80": (80, "Actual"),
    "forecast100": (100, "Forecasted"),
}


def validate_template(template: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    expected_schema = "https://schema.management.azure.com/schemas/2018-05-01/subscriptionDeploymentTemplate.json#"
    if template.get("$schema") != expected_schema:
        failures.append("compiled budget template uses an unexpected subscription schema")
    resources = [resource for resource in template.get("resources") or [] if isinstance(resource, dict)]
    if len(resources) != 1 or resources[0].get("type") != "Microsoft.Consumption/budgets":
        failures.append("budget template must contain exactly one Microsoft.Consumption/budgets resource")
        return failures
    properties = resources[0].get("properties") or {}
    if properties.get("category") != "Cost" or properties.get("timeGrain") != "Monthly":
        failures.append("budget category or time grain differs")
    amount = str(properties.get("amount", "")).casefold()
    start_date = str((properties.get("timePeriod") or {}).get("startDate", "")).casefold()
    if "monthlyamount" not in amount:
        failures.append("budget amount must come from the bounded monthlyAmount parameter")
    if "startdate" not in start_date:
        failures.append("budget start date must come from materialized runtime parameters")
    notifications = properties.get("notifications") or {}
    if set(notifications) != set(EXPECTED_NOTIFICATIONS):
        failures.append("budget notification inventory differs")
        return failures
    for name, (threshold, threshold_type) in EXPECTED_NOTIFICATIONS.items():
        notification = notifications[name]
        expected = {
            "contactGroups": [],
            "contactRoles": ["Owner"],
            "enabled": True,
            "locale": "en-us",
            "operator": "GreaterThanOrEqualTo",
            "threshold": threshold,
            "thresholdType": threshold_type,
        }
        if any(notification.get(key) != value for key, value in expected.items()):
            failures.append(f"budget notification differs: {name}")
        emails = notification.get("contactEmails") or []
        if len(emails) != 1 or "notificationemail" not in str(emails[0]).casefold():
            failures.append(f"budget notification must use the secure runtime recipient: {name}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read compiled budget template: {error}", file=sys.stderr)
        return 1
    failures = validate_template(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS budget template has one bounded monthly cost resource")
    print("PASS 50%, 80%, and forecasted 100% notifications use a secure runtime recipient")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())