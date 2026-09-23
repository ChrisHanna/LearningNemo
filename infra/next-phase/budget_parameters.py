#!/usr/bin/env python3
"""Validate budget configuration and materialize runtime-only ARM parameters."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from foundation_parameters import PARAMETER_SCHEMA


EXPECTED_FIELDS = {"schemaVersion", "location", "environment", "budgetName", "monthlyAmount"}
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._()\-]{1,63}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class BudgetParameterError(RuntimeError):
    pass


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BudgetParameterError(f"unable to read budget configuration: {error}") from error
    if not isinstance(config, dict) or set(config) != EXPECTED_FIELDS:
        raise BudgetParameterError("budget configuration fields do not match the contract")
    if config.get("schemaVersion") != 1:
        raise BudgetParameterError("budget configuration schemaVersion must be 1")
    for name in ("location", "environment", "budgetName"):
        if not isinstance(config.get(name), str) or not config[name]:
            raise BudgetParameterError(f"budget field {name} must be a non-empty string")
    if NAME_PATTERN.fullmatch(config["budgetName"]) is None:
        raise BudgetParameterError("budgetName contains unsupported characters")
    amount = config.get("monthlyAmount")
    if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 100:
        raise BudgetParameterError("monthlyAmount must be an integer from 1 through 100")
    return config


def validate_start_date(value: str) -> str:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise BudgetParameterError("budget startDate must use ISO-8601 format") from error
    if parsed.day != 1 or any((parsed.hour, parsed.minute, parsed.second, parsed.microsecond)):
        raise BudgetParameterError("budget startDate must be midnight on the first day of a month")
    return parsed.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_email(value: str) -> str:
    if len(value) > 254 or EMAIL_PATTERN.fullmatch(value) is None:
        raise BudgetParameterError("runtime budget notification email is invalid")
    return value


def materialize(config_path: Path, output: Path, start_date: str, notification_email: str) -> None:
    config = load_config(config_path)
    document = {
        "$schema": PARAMETER_SCHEMA,
        "contentVersion": "1.0.0.0",
        "parameters": {
            "budgetName": {"value": config["budgetName"]},
            "monthlyAmount": {"value": config["monthlyAmount"]},
            "startDate": {"value": validate_start_date(start_date)},
            "notificationEmail": {"value": validate_email(notification_email)},
        },
    }
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("config", type=Path)
    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("config", type=Path)
    get_parser.add_argument("name", choices=sorted(EXPECTED_FIELDS - {"schemaVersion"}))
    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("config", type=Path)
    materialize_parser.add_argument("output", type=Path)
    materialize_parser.add_argument("--start-date", required=True)
    materialize_parser.add_argument("--notification-email-file", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "validate":
            load_config(args.config)
            print("PASS budget configuration is valid and contains no notification recipient")
        elif args.command == "get":
            print(load_config(args.config)[args.name])
        else:
            try:
                notification_email = args.notification_email_file.read_text(encoding="utf-8").strip()
            except OSError as error:
                raise BudgetParameterError("unable to read runtime budget recipient") from error
            materialize(args.config, args.output, args.start_date, notification_email)
            print("PASS materialized secure runtime budget parameters", file=sys.stderr)
        return 0
    except BudgetParameterError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())