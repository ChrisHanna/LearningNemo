#!/usr/bin/env python3
"""Validate only allowlisted evidence from an authorized target-database login probe."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_LINES = {
    ("PASS", "sql_private_dns"),
    ("PASS", "sql_private_tcp"),
    ("PASS", "sql_managed_identity_token"),
    ("FAIL", "sql_unexpected_authorization"),
}
RESULT_PATTERN = re.compile(r"\b(PASS|FAIL) ([a-z0-9_]+)\b")
SAFE_CATEGORIES = {
    "probe_configuration_failed",
    "sql_access_token_attribute_failed",
    "sql_authentication_rejected_as_expected",
    "sql_dns_not_private",
    "sql_managed_identity_token",
    "sql_managed_identity_token_failed",
    "sql_odbc_environment_failed",
    "sql_odbc_link_failed",
    "sql_odbc_login_timeout",
    "sql_odbc_prelogin_failed",
    "sql_odbc_prelogin_tls",
    "sql_odbc_tls_failed",
    "sql_odbc_unknown_failed",
    "sql_private_dns",
    "sql_private_tcp",
    "sql_tcp_connect_failed",
    "sql_tcp_connect_timed_out",
    "sql_unexpected_authorization",
}


class SqlAdminProbeSummaryError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise SqlAdminProbeSummaryError("SQL admin probe evidence is unreadable") from error
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return content


def string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in string_values(child)]
    if isinstance(value, list):
        return [text for child in value for text in string_values(child)]
    return []


def observations(execution: Any, logs: Any) -> tuple[str | None, set[tuple[str, str]]]:
    status = (execution.get("properties") or {}).get("status") if isinstance(execution, dict) else None
    results = {
        (match.group(1), match.group(2))
        for text in string_values(logs)
        for match in RESULT_PATTERN.finditer(text)
        if match.group(2) in SAFE_CATEGORIES
    }
    return status if isinstance(status, str) else None, results


def summarize(execution: Any, logs: Any) -> None:
    status, results = observations(execution, logs)
    if status != "Failed" or results != EXPECTED_LINES:
        raise SqlAdminProbeSummaryError("SQL admin target login probe did not match its closed evidence contract")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    args = parser.parse_args()
    execution = load_json(args.execution)
    logs = load_json(args.logs)
    try:
        summarize(execution, logs)
    except SqlAdminProbeSummaryError as error:
        status, results = observations(execution, logs)
        print(f"INFO SQL admin probe execution status: {status or 'unavailable'}")
        for outcome, category in sorted(results):
            print(f"INFO SQL admin probe observed: {outcome.casefold()} {category}")
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    print("PASS SQL admin reached and authenticated to the target database")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())