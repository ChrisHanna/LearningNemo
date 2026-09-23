#!/usr/bin/env python3
"""Prove private SQL ODBC prelogin/TLS using an intentionally unauthorized identity."""

from __future__ import annotations

import os
import re
import socket
import sys
import time
import uuid

from azure.identity import ManagedIdentityCredential
from mssql_python import connect

from task_agent.control.mssql_client import build_connection_string
from task_agent.control.mssql_client import require_private_sql_resolution


NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SQL_SCOPE = "https://database.windows.net/.default"
EXPECTED_REJECTION_MARKERS = (
    "18456",
    "invalid authorization specification",
    "login failed",
    "sqlstate:28000",
)
TCP_ATTEMPTS = 3
TCP_TIMEOUT_SECONDS = 10
TCP_RETRY_SECONDS = 5


class OdbcProbeError(RuntimeError):
    pass


def failure_text(error: BaseException) -> str:
    values: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        values.append(str(current))
        for attribute in ("driver_error", "ddbc_error"):
            value = getattr(current, attribute, None)
            if isinstance(value, str):
                values.append(value)
        current = current.__cause__ or current.__context__
    return " ".join(values).casefold()


def classify_odbc_failure(error: BaseException) -> str:
    text = failure_text(error)
    if any(marker in text for marker in EXPECTED_REJECTION_MARKERS):
        return "sql_authentication_rejected_as_expected"
    if any(marker in text for marker in ("ssl provider", "certificate verify", "certificate chain")):
        return "sql_odbc_tls_failed"
    if any(marker in text for marker in ("failed to allocate environment handle", "failed to set environment")):
        return "sql_odbc_environment_failed"
    if "failed to set attribute 1256 before connect" in text:
        return "sql_access_token_attribute_failed"
    if "08001" in text:
        return "sql_odbc_prelogin_failed"
    if any(marker in text for marker in ("08s01", "communication link failure")):
        return "sql_odbc_link_failed"
    if any(marker in text for marker in ("hyt00", "hyt01", "login timeout")):
        return "sql_odbc_login_timeout"
    return "sql_odbc_unknown_failed"


def require_raw_tcp(address: str) -> None:
    last_error: OSError | None = None
    for attempt in range(TCP_ATTEMPTS):
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(TCP_TIMEOUT_SECONDS)
        try:
            connection.connect((address, 1433))
            return
        except OSError as error:
            last_error = error
            if attempt + 1 < TCP_ATTEMPTS:
                time.sleep(TCP_RETRY_SECONDS)
        finally:
            connection.close()
    if isinstance(last_error, TimeoutError):
        raise OdbcProbeError("sql_tcp_connect_timed_out") from last_error
    raise OdbcProbeError("sql_tcp_connect_failed") from last_error


def main() -> int:
    server = os.environ.get("LEARNINGNEMO_SQL_SERVER", "")
    database = os.environ.get("LEARNINGNEMO_SQL_DATABASE", "")
    client_id = os.environ.get("AZURE_CLIENT_ID", "")
    try:
        uuid.UUID(client_id)
        if NAME_PATTERN.fullmatch(database) is None:
            raise OdbcProbeError("probe_configuration_failed")
        address = require_private_sql_resolution(server)[0]
        print("PASS sql_private_dns")
        require_raw_tcp(address)
        print("PASS sql_private_tcp")
        credential = ManagedIdentityCredential(client_id=client_id)
        try:
            token = credential.get_token(SQL_SCOPE)
            if not isinstance(getattr(token, "token", None), str) or not token.token:
                raise OdbcProbeError("sql_managed_identity_token_failed")
            print("PASS sql_managed_identity_token")
            connection_string = build_connection_string(
                server=server,
                database=database,
                client_id=client_id,
                application_name="LearningNeMoOdbcProbe",
            )
            try:
                connection = connect(connection_string, token_provider=credential, timeout=120)
            except Exception as error:
                category = classify_odbc_failure(error)
                if category == "sql_authentication_rejected_as_expected":
                    print("PASS sql_odbc_prelogin_tls")
                    print("PASS sql_authentication_rejected_as_expected")
                    return 0
                raise OdbcProbeError(category) from error
            else:
                connection.close()
                raise OdbcProbeError("sql_unexpected_authorization")
        finally:
            credential.close()
    except (OdbcProbeError, ValueError) as error:
        category = str(error) if isinstance(error, OdbcProbeError) else "probe_configuration_failed"
        print(f"FAIL {category}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())