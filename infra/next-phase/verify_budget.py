#!/usr/bin/env python3
"""Verify the deployed subscription budget without printing recipients or IDs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from budget_parameters import BudgetParameterError
from budget_parameters import load_config
from preflight_platform import budget_is_active


EXPECTED_NOTIFICATIONS = {
    "actual50": (50, "Actual"),
    "actual80": (80, "Actual"),
    "forecast100": (100, "Forecasted"),
}


class VerificationError(RuntimeError):
    pass


def az_json(arguments: list[str], *, timeout: int = 60) -> Any:
    try:
        result = subprocess.run(
            ["az", *arguments, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VerificationError("Azure budget query failed or timed out") from error
    if result.returncode != 0:
        raise VerificationError("Azure budget query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise VerificationError("Azure budget query returned unreadable JSON") from error


def verify_notifications(notifications: Any) -> None:
    if not isinstance(notifications, dict) or set(notifications) != set(EXPECTED_NOTIFICATIONS):
        raise VerificationError("budget notification inventory differs")
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
        if not isinstance(notification, dict) or any(
            notification.get(key) != value for key, value in expected.items()
        ):
            raise VerificationError(f"budget notification differs: {name}")
        emails = notification.get("contactEmails") or []
        if len(emails) != 1 or not isinstance(emails[0], str) or "@" not in emails[0]:
            raise VerificationError(f"budget notification recipient differs: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        try:
            config = load_config(args.config)
        except BudgetParameterError as error:
            raise VerificationError(str(error)) from error
        account = az_json(["account", "show"], timeout=20)
        expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
        if expected_subscription and account.get("id") != expected_subscription:
            raise VerificationError("active subscription mismatch")
        subscription_id = str(account.get("id", ""))
        if not subscription_id:
            raise VerificationError("active subscription identifier is unavailable")
        budget = az_json(
            [
                "rest",
                "--method",
                "GET",
                "--url",
                (
                    "https://management.azure.com/subscriptions/"
                    f"{subscription_id}/providers/Microsoft.Consumption/budgets/"
                    f"{config['budgetName']}?api-version=2024-08-01"
                ),
            ]
        )
        properties = budget.get("properties") or {}
        if properties.get("amount") != config["monthlyAmount"]:
            raise VerificationError("budget amount differs from the checked-in cost ceiling")
        if properties.get("category") != "Cost" or properties.get("timeGrain") != "Monthly":
            raise VerificationError("budget category or time grain differs")
        if not budget_is_active(budget, dt.datetime.now(dt.UTC).date()):
            raise VerificationError("budget is not active or has no enabled notification")
        verify_notifications(properties.get("notifications"))
        print("PASS monthly subscription budget amount and active period match")
        print("PASS 50%, 80%, and forecasted 100% notifications match")
        print("PASS runtime recipient exists and was not printed")
        return 0
    except VerificationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())