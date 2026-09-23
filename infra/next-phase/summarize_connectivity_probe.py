#!/usr/bin/env python3
"""Record only allowlisted connectivity-probe results from Container Apps logs."""

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


FAILURE_REASONS = {
    "probe_configuration_failed",
    "sql_private_dns_failed",
    "sql_dns_address_count_invalid",
    "sql_dns_not_private",
    "sql_tcp_connect_timed_out",
    "sql_tcp_connection_refused",
    "sql_tcp_network_unreachable",
    "sql_tcp_connect_blocked",
    "sql_tcp_address_failed",
    "sql_tcp_connection_reset",
    "sql_tcp_os_error_unknown",
    "sql_managed_identity_token_failed",
    "sql_access_token_attribute_failed",
    "sql_odbc_environment_failed",
    "sql_odbc_tls_failed",
    "sql_odbc_prelogin_failed",
    "sql_odbc_link_failed",
    "sql_odbc_login_timeout",
    "sql_odbc_unknown_failed",
    "sql_unexpected_authorization",
    *(f"sql_tcp_os_error_{name.casefold()}" for name in errno.errorcode.values()),
}
FAILURE_PATTERN = re.compile(r"\bFAIL\s+(" + "|".join(sorted(FAILURE_REASONS)) + r")\b")
PASS_PATTERN = re.compile(
    r"\bPASS\s+(sql_private_dns|sql_private_tcp|sql_managed_identity_token|"
    r"sql_odbc_prelogin_tls|sql_authentication_rejected_as_expected)\b"
)
EXIT_KEYS = {"exitcode", "exit_code", "containerexitcode"}


class ConnectivityProbeSummaryError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ConnectivityProbeSummaryError("connectivity probe evidence is unreadable") from error
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


def summarize(
    execution: Any,
    logs: Any,
) -> tuple[str, bool, bool, bool, bool, bool, str | None, int | None]:
    texts = string_values(logs)
    passes = {match.group(1) for text in texts for match in PASS_PATTERN.finditer(text)}
    failures = {match.group(1) for text in texts for match in FAILURE_PATTERN.finditer(text)}
    codes = {code for code in exit_codes(execution) if code != 0}
    dns_private = "sql_private_dns" in passes
    tcp_reachable = "sql_private_tcp" in passes
    token_acquired = "sql_managed_identity_token" in passes
    odbc_prelogin_tls = "sql_odbc_prelogin_tls" in passes
    authentication_rejected = "sql_authentication_rejected_as_expected" in passes
    if all((dns_private, tcp_reachable, token_acquired, odbc_prelogin_tls, authentication_rejected)) and not failures:
        return "passed", True, True, True, True, True, None, None
    failure = next(iter(failures)) if len(failures) == 1 else "unavailable"
    code = next(iter(codes)) if len(codes) == 1 else None
    return (
        "failed",
        dns_private,
        tcp_reachable,
        token_acquired,
        odbc_prelogin_tls,
        authentication_rejected,
        failure,
        code,
    )


def write_report(
    output: Path,
    status: str,
    dns_private: bool,
    tcp_reachable: bool,
    token_acquired: bool,
    odbc_prelogin_tls: bool,
    authentication_rejected: bool,
    failure: str | None,
    code: int | None,
    *,
    recorded_at: dt.datetime | None = None,
) -> None:
    current = (recorded_at or dt.datetime.now(dt.UTC)).astimezone(dt.UTC).replace(microsecond=0)
    report = {
        "schemaVersion": 1,
        "status": status,
        "privateDnsPassed": dns_private,
        "tcp1433Passed": tcp_reachable,
        "managedIdentityTokenPassed": token_acquired,
        "odbcPreloginTlsPassed": odbc_prelogin_tls,
        "authenticationRejectedAsExpected": authentication_rejected,
        "failureCategory": failure,
        "processExitCode": code,
        "recordedAt": current.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = summarize(load_json(args.execution), load_json(args.logs))
        write_report(args.output, *result)
    except ConnectivityProbeSummaryError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    (
        status,
        dns_private,
        tcp_reachable,
        token_acquired,
        odbc_prelogin_tls,
        authentication_rejected,
        failure,
        code,
    ) = result
    print(f"INFO connectivity probe status: {status}")
    print(f"INFO connectivity probe private DNS: {'passed' if dns_private else 'failed'}")
    print(f"INFO connectivity probe TCP 1433: {'passed' if tcp_reachable else 'failed'}")
    print(f"INFO connectivity probe managed identity token: {'passed' if token_acquired else 'failed'}")
    print(f"INFO connectivity probe ODBC prelogin TLS: {'passed' if odbc_prelogin_tls else 'failed'}")
    print(
        "INFO connectivity probe expected authorization rejection: "
        + ("passed" if authentication_rejected else "failed")
    )
    if failure is not None:
        print(f"INFO connectivity probe failure category: {failure}")
    print(f"INFO connectivity probe process exit code: {code if code is not None else 'unavailable'}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())