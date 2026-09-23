#!/usr/bin/env python3
"""Report only allowlisted diagnostics from a failed SQL migration execution."""

from __future__ import annotations

import argparse
import datetime as dt
import errno
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


TCP_ERRNO_REASONS = {
    *(f"sql_tcp_os_error_{name.casefold()}" for name in errno.errorcode.values()),
    "sql_tcp_os_error_unknown",
}
FAILURE_REASONS = {
    "driver_import_failed",
    "sql_private_dns_failed",
    "sql_dns_not_private",
    "sql_managed_identity_token_failed",
    "sql_admin_master_connect_failed",
    "sql_target_database_connect_failed",
    "sql_native_runtime_failed",
    "sql_connection_configuration_failed",
    "sql_access_token_attribute_failed",
    "sql_odbc_connect_failed",
    "sql_odbc_client_connect_failed",
    "sql_odbc_environment_failed",
    "sql_odbc_handle_failed",
    "sql_odbc_prelogin_failed",
    "sql_driver_not_found",
    "sql_tcp_connect_failed",
    "sql_tcp_connect_timed_out",
    "sql_tcp_connect_blocked",
    "sql_tcp_connection_refused",
    "sql_tcp_connection_reset",
    "sql_tcp_address_failed",
    "sql_tcp_network_unreachable",
    "sql_server_rejected_connection",
    "sql_link_failed",
    "sql_login_timeout",
    "sql_tls_failed",
    "sql_driver_general_failed",
    "migration_receipt_failed",
    "sql_authentication_failed",
    "sql_network_failed",
    "sql_execution_failed",
    "migration_unknown_failed",
} | TCP_ERRNO_REASONS
FAILURE_PATTERN = re.compile(r"\bFAIL\s+(" + "|".join(sorted(FAILURE_REASONS)) + r")\b")
EXIT_KEYS = {"exitcode", "exit_code", "containerexitcode"}


class MigrationFailureSummaryError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise MigrationFailureSummaryError("migration failure evidence is unreadable") from error
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


def exit_codes(value: Any) -> set[int]:
    found: set[int] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z_]", "", str(key).casefold())
            if normalized in EXIT_KEYS and isinstance(child, int) and 0 <= child <= 255:
                found.add(child)
            found.update(exit_codes(child))
    elif isinstance(value, list):
        for child in value:
            found.update(exit_codes(child))
    return found


def summarize(execution: Any, logs: Any) -> tuple[str | None, int | None]:
    reasons = {
        match.group(1)
        for text in string_values(logs)
        for match in FAILURE_PATTERN.finditer(text)
    }
    codes = {code for code in exit_codes(execution) if code != 0}
    reason = next(iter(reasons)) if len(reasons) == 1 else None
    code = next(iter(codes)) if len(codes) == 1 else None
    return reason, code


def write_report(
    output: Path,
    reason: str | None,
    code: int | None,
    *,
    recorded_at: dt.datetime | None = None,
) -> None:
    current = (recorded_at or dt.datetime.now(dt.UTC)).astimezone(dt.UTC).replace(microsecond=0)
    report = {
        "schemaVersion": 1,
        "status": "failed",
        "failureCategory": reason or "unavailable",
        "processExitCode": code,
        "recordedAt": current.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        reason, code = summarize(load_json(args.execution), load_json(args.logs))
    except MigrationFailureSummaryError as error:
        print(f"INFO {error}")
        return 0
    if args.output is not None:
        write_report(args.output, reason, code)
    print(f"INFO migration process failure category: {reason or 'unavailable'}")
    print(f"INFO migration process exit code: {code if code is not None else 'unavailable'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())