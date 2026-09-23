#!/usr/bin/env python3
"""Apply a hash-bound LearningNeMo migration bundle with managed identity."""

from __future__ import annotations

import hashlib
import errno
import ipaddress
import os
import re
import socket
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from task_agent.control.mssql_client import build_connection_string
from task_agent.control.mssql_client import managed_identity_connect
from task_agent.control.mssql_client import require_private_sql_resolution


SHA256 = re.compile(r"^[0-9a-f]{64}$")
MIGRATION_HEADER = re.compile(
    r"(?m)^-- BEGIN MIGRATION ([A-Za-z0-9._-]+) sha256=([0-9a-f]{64})$"
)
VERIFY_MIGRATIONS = """
SELECT MigrationId, ContentHash
FROM control.SchemaMigrations
WHERE MigrationId IN (
    N'001_schema.sql',
    N'002_procedures.sql',
    N'003_seed.sql',
    N'004_security.sql',
    N'005_control_workflow.sql',
    N'006_reconcile_expired_query_runs.sql'
)
ORDER BY MigrationId;
"""
EXIT_IMPORT = 20
EXIT_NETWORK = 21
EXIT_AUTHENTICATION = 22
EXIT_SQL = 23
EXIT_RECEIPT = 24
EXIT_UNKNOWN = 25
EXIT_NATIVE = 26
EXIT_CONFIGURATION = 27
EXIT_DRIVER_NOT_FOUND = 28
EXIT_TCP_CONNECT = 29
EXIT_SERVER_REJECTED = 30
EXIT_LINK_FAILURE = 31
EXIT_LOGIN_TIMEOUT = 32
EXIT_DRIVER_GENERAL = 33
EXIT_DNS = 34
EXIT_PUBLIC_DNS = 35
EXIT_TOKEN = 36
EXIT_ODBC_CONNECT = 37
EXIT_TLS = 38
EXIT_TOKEN_ATTRIBUTE = 39
EXIT_ODBC_ENVIRONMENT = 40
EXIT_ODBC_HANDLE = 41
EXIT_TCP_TIMEOUT = 42
EXIT_TCP_REFUSED = 43
EXIT_TCP_UNREACHABLE = 44
EXIT_TCP_BLOCKED = 45
EXIT_TCP_ADDRESS = 46
EXIT_TCP_RESET = 47
EXIT_ODBC_PRELOGIN = 48
EXIT_ADMIN_MASTER = 49
EXIT_TARGET_DATABASE = 50
SQL_SCOPE = "https://database.windows.net/.default"
SQL_LOGIN_TIMEOUT_SECONDS = 120
SQL_TCP_PROBE_ATTEMPTS = 6
SQL_TCP_PROBE_RETRY_SECONDS = 5
SQL_TARGET_CONNECT_ATTEMPTS = 3
SQL_TARGET_CONNECT_RETRY_SECONDS = 15
TRANSIENT_TARGET_CONNECT_MARKERS = (
    "08001",
    "08s01",
    "hyt00",
    "hyt01",
    "40197",
    "40501",
    "40613",
    "communication link failure",
    "connection reset",
    "login timeout",
)


class MigrationStageError(RuntimeError):
    pass


def load_bundle(path: Path, expected_hash: str) -> str:
    if SHA256.fullmatch(expected_hash) is None:
        raise RuntimeError("SQL migration hash is invalid")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise RuntimeError("SQL migration bundle hash differs")
    return content.decode("utf-8")


def split_batches(sql: str) -> tuple[str, ...]:
    batches = tuple(
        batch.strip()
        for batch in re.split(r"(?im)^\s*GO\s*$", sql)
        if batch.strip()
    )
    if not batches:
        raise RuntimeError("SQL migration bundle contains no batches")
    return batches


def expected_migrations(sql: str) -> dict[str, str]:
    migrations = dict(MIGRATION_HEADER.findall(sql))
    if set(migrations) != {
        "001_schema.sql",
        "002_procedures.sql",
        "003_seed.sql",
        "004_security.sql",
        "005_control_workflow.sql",
        "006_reconcile_expired_query_runs.sql",
    }:
        raise RuntimeError("SQL migration bundle inventory differs")
    return migrations


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


def classify_failure(error: BaseException) -> tuple[str, int]:
    text = failure_text(error)
    if isinstance(error, (ImportError, ModuleNotFoundError)):
        return "driver_import_failed", EXIT_IMPORT
    if "sql_private_dns_resolution_failed" in text:
        return "sql_private_dns_failed", EXIT_DNS
    if "sql_dns_resolved_public_address" in text:
        return "sql_dns_not_private", EXIT_PUBLIC_DNS
    if "sql_tcp_probe_failed" in text:
        return "sql_tcp_connect_failed", EXIT_TCP_CONNECT
    if "sql_tcp_probe_timed_out" in text:
        return "sql_tcp_connect_timed_out", EXIT_TCP_TIMEOUT
    if "sql_tcp_probe_refused" in text:
        return "sql_tcp_connection_refused", EXIT_TCP_REFUSED
    if "sql_tcp_probe_unreachable" in text:
        return "sql_tcp_network_unreachable", EXIT_TCP_UNREACHABLE
    if "sql_tcp_probe_blocked" in text:
        return "sql_tcp_connect_blocked", EXIT_TCP_BLOCKED
    if "sql_tcp_probe_address_failed" in text:
        return "sql_tcp_address_failed", EXIT_TCP_ADDRESS
    if "sql_tcp_probe_reset" in text:
        return "sql_tcp_connection_reset", EXIT_TCP_RESET
    if "sql_managed_identity_token_failed" in text:
        return "sql_managed_identity_token_failed", EXIT_TOKEN
    if "sql_admin_master_connect_failed" in text:
        return "sql_admin_master_connect_failed", EXIT_ADMIN_MASTER
    if "sql_target_database_connect_failed" in text:
        return "sql_target_database_connect_failed", EXIT_TARGET_DATABASE
    if any(marker in text for marker in ("shared library", "dlopen", "symbol not found", ".so:")):
        return "sql_native_runtime_failed", EXIT_NATIVE
    if "failed to set attribute 1256 before connect" in text:
        return "sql_access_token_attribute_failed", EXIT_TOKEN_ATTRIBUTE
    if any(marker in text for marker in ("failed to allocate environment handle", "failed to set environment attributes")):
        return "sql_odbc_environment_failed", EXIT_ODBC_ENVIRONMENT
    if any(marker in text for marker in ("connection handle not allocated", "connection object is not initialized")):
        return "sql_odbc_handle_failed", EXIT_ODBC_HANDLE
    if any(marker in text for marker in ("connection string parsing", "invalid connection string", "invalid keyword")):
        return "sql_connection_configuration_failed", EXIT_CONFIGURATION
    for sqlstate, reason, exit_code in (
        ("im002", "sql_driver_not_found", EXIT_DRIVER_NOT_FOUND),
        ("08001", "sql_odbc_prelogin_failed", EXIT_ODBC_PRELOGIN),
        ("08004", "sql_server_rejected_connection", EXIT_SERVER_REJECTED),
        ("08s01", "sql_link_failed", EXIT_LINK_FAILURE),
        ("hyt00", "sql_login_timeout", EXIT_LOGIN_TIMEOUT),
        ("hyt01", "sql_login_timeout", EXIT_LOGIN_TIMEOUT),
        ("hy000", "sql_driver_general_failed", EXIT_DRIVER_GENERAL),
    ):
        if sqlstate in text:
            return reason, exit_code
    if "invalid authorization specification" in text:
        return "sql_authentication_failed", EXIT_AUTHENTICATION
    if "server rejected the connection" in text:
        return "sql_server_rejected_connection", EXIT_SERVER_REJECTED
    if "communication link failure" in text:
        return "sql_link_failed", EXIT_LINK_FAILURE
    if "login timeout" in text:
        return "sql_login_timeout", EXIT_LOGIN_TIMEOUT
    if any(marker in text for marker in ("ssl provider", "certificate verify", "certificate chain")):
        return "sql_tls_failed", EXIT_TLS
    if any(marker in text for marker in ("client unable to establish connection", "tcp provider")):
        return "sql_odbc_client_connect_failed", EXIT_ODBC_CONNECT
    if "migration receipt differs" in text:
        return "migration_receipt_failed", EXIT_RECEIPT
    if any(marker in text for marker in ("18456", "login failed", "authentication", "aadsts", "managed identity")):
        return "sql_authentication_failed", EXIT_AUTHENTICATION
    if "sql_odbc_connect_failed" in text:
        return "sql_odbc_connect_failed", EXIT_ODBC_CONNECT
    if any(
        marker in text
        for marker in (
            "08001",
            "08003",
            "08004",
            "08007",
            "08s01",
            "connection",
            "network",
            "resolve",
            "timeout",
            "timed out",
        )
    ):
        return "sql_network_failed", EXIT_NETWORK
    if any(marker in text for marker in ("sqlstate", "42000", "42s", "incorrect syntax", "invalid object")):
        return "sql_execution_failed", EXIT_SQL
    return "migration_unknown_failed", EXIT_UNKNOWN


def require_private_sql_tcp(
    addresses: tuple[str, ...],
    *,
    timeout_seconds: int = 10,
    attempts: int = SQL_TCP_PROBE_ATTEMPTS,
    retry_seconds: int = SQL_TCP_PROBE_RETRY_SECONDS,
    connect: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if attempts < 1 or retry_seconds < 0:
        raise ValueError("SQL TCP probe retry configuration is invalid")
    if len(addresses) != 1 or not any(
        ipaddress.ip_address(addresses[0]) in network
        for network in (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )
    ):
        raise MigrationStageError("sql_private_dns_resolution_failed")
    if connect is None:
        def connect(address: tuple[str, int], *, timeout: int) -> socket.socket:
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            connection.settimeout(timeout)
            try:
                connection.connect(address)
            except BaseException:
                connection.close()
                raise
            return connection
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            connection = connect((addresses[0], 1433), timeout=timeout_seconds)
            connection.close()
            return
        except OSError as error:
            last_error = error
            if attempt + 1 < attempts:
                sleep(retry_seconds)
    if isinstance(last_error, TimeoutError):
        marker = "sql_tcp_probe_timed_out"
    elif last_error is not None and last_error.errno in {errno.ETIMEDOUT}:
        marker = "sql_tcp_probe_timed_out"
    elif last_error is not None and last_error.errno == errno.ECONNREFUSED:
        marker = "sql_tcp_probe_refused"
    elif last_error is not None and last_error.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH}:
        marker = "sql_tcp_probe_unreachable"
    elif last_error is not None and last_error.errno in {errno.EACCES, errno.EPERM}:
        marker = "sql_tcp_probe_blocked"
    elif last_error is not None and last_error.errno in {
        errno.EADDRNOTAVAIL,
        errno.EAFNOSUPPORT,
        errno.EINVAL,
        errno.EPROTONOSUPPORT,
    }:
        marker = "sql_tcp_probe_address_failed"
    elif last_error is not None and last_error.errno in {
        errno.ECONNABORTED,
        errno.ECONNRESET,
        errno.EPIPE,
    }:
        marker = "sql_tcp_probe_reset"
    else:
        errno_name = errno.errorcode.get(getattr(last_error, "errno", None))
        marker = (
            f"sql_tcp_os_error_{errno_name.casefold()}"
            if errno_name is not None
            else "sql_tcp_os_error_unknown"
        )
    raise MigrationStageError(marker) from last_error


def require_managed_identity_sql_token(
    client_id: str,
    *,
    credential_factory: Callable[..., Any] | None = None,
) -> Any:
    if credential_factory is None:
        from azure.identity import ManagedIdentityCredential

        credential_factory = ManagedIdentityCredential
    credential = credential_factory(client_id=client_id)
    try:
        token = credential.get_token(SQL_SCOPE)
        if not isinstance(getattr(token, "token", None), str) or not token.token:
            raise MigrationStageError("sql_managed_identity_token_failed")
        return token
    except MigrationStageError:
        raise
    except Exception as error:
        raise MigrationStageError("sql_managed_identity_token_failed") from error
    finally:
        close = getattr(credential, "close", None)
        if callable(close):
            close()


class VerifiedTokenProvider:
    def __init__(self, token: Any) -> None:
        self._token = token

    def get_token(self, scope: str) -> Any:
        if scope != SQL_SCOPE:
            raise RuntimeError("sql_token_scope_failed")
        return self._token


def connect_target_database(
    connect: Callable[[], Any],
    *,
    attempts: int = SQL_TARGET_CONNECT_ATTEMPTS,
    retry_seconds: int = SQL_TARGET_CONNECT_RETRY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    if attempts < 1 or retry_seconds < 0:
        raise ValueError("SQL target connection retry configuration is invalid")
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            return connect()
        except Exception as error:
            last_error = error
            transient = any(marker in failure_text(error) for marker in TRANSIENT_TARGET_CONNECT_MARKERS)
            if not transient or attempt + 1 == attempts:
                break
            sleep(retry_seconds)
    raise MigrationStageError("sql_target_database_connect_failed") from last_error


def apply_migrations() -> None:
    bundle_path = Path(os.environ["LEARNINGNEMO_SQL_MIGRATION_BUNDLE"])
    expected_hash = os.environ["LEARNINGNEMO_SQL_MIGRATION_SHA256"]
    server = os.environ["LEARNINGNEMO_SQL_SERVER"]
    database = os.environ["LEARNINGNEMO_SQL_DATABASE"]
    client_id = os.environ["AZURE_CLIENT_ID"]
    uuid.UUID(client_id)
    sql = load_bundle(bundle_path, expected_hash)
    expected = expected_migrations(sql)
    connection_string = build_connection_string(
        server=server,
        database=database,
        client_id=client_id,
        application_name="LearningNeMoSqlMigrator",
    )
    private_addresses = require_private_sql_resolution(server)
    require_private_sql_tcp(private_addresses)
    token_provider = VerifiedTokenProvider(require_managed_identity_sql_token(client_id))
    provider_factory = lambda **_options: token_provider
    connection = connect_target_database(
        lambda: managed_identity_connect(
            connection_string,
            client_id,
            timeout_seconds=SQL_LOGIN_TIMEOUT_SECONDS,
            server=server,
            credential_factory=provider_factory,
        )
    )
    with connection:
        with connection.cursor() as cursor:
            if hasattr(cursor, "timeout"):
                cursor.timeout = 120
            for batch in split_batches(sql):
                cursor.execute(batch)
            cursor.execute(VERIFY_MIGRATIONS)
            applied = {str(row[0]): str(row[1]) for row in cursor.fetchall()}
            if applied != expected:
                raise RuntimeError("Azure SQL migration receipt differs")
        connection.commit()


def main() -> int:
    try:
        apply_migrations()
    except Exception as error:
        reason, exit_code = classify_failure(error)
        print(f"FAIL {reason}", file=sys.stderr)
        return exit_code
    print("PASS applied hash-bound Azure SQL migration bundle with managed identity")
    print("PASS verified six exact Azure SQL migration receipts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())