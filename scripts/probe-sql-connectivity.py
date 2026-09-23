#!/usr/bin/env python3
"""Probe private SQL DNS and TCP connectivity without authenticating to SQL."""

from __future__ import annotations

import errno
import ipaddress
import os
import re
import socket
import sys
import time
from collections.abc import Callable
from typing import Any


SERVER_PATTERN = re.compile(r"^[a-z0-9-]{1,63}\.database\.windows\.net$")
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
PROBE_ATTEMPTS = 3
PROBE_TIMEOUT_SECONDS = 10
PROBE_RETRY_SECONDS = 5


class ConnectivityProbeError(RuntimeError):
    pass


def is_rfc1918(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return parsed.version == 4 and any(parsed in network for network in PRIVATE_NETWORKS)


def resolve_private_address(
    server: str,
    *,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> str:
    if SERVER_PATTERN.fullmatch(server) is None:
        raise ConnectivityProbeError("probe_configuration_failed")
    try:
        records = resolver(server, 1433, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ConnectivityProbeError("sql_private_dns_failed") from error
    addresses = tuple(sorted({str(record[4][0]) for record in records}))
    if len(addresses) != 1:
        raise ConnectivityProbeError("sql_dns_address_count_invalid")
    if not is_rfc1918(addresses[0]):
        raise ConnectivityProbeError("sql_dns_not_private")
    return addresses[0]


def tcp_failure_category(error: OSError | None) -> str:
    if isinstance(error, TimeoutError) or getattr(error, "errno", None) == errno.ETIMEDOUT:
        return "sql_tcp_connect_timed_out"
    categories = {
        errno.ECONNREFUSED: "sql_tcp_connection_refused",
        errno.ENETUNREACH: "sql_tcp_network_unreachable",
        errno.EHOSTUNREACH: "sql_tcp_network_unreachable",
        errno.EACCES: "sql_tcp_connect_blocked",
        errno.EPERM: "sql_tcp_connect_blocked",
        errno.EADDRNOTAVAIL: "sql_tcp_address_failed",
        errno.EAFNOSUPPORT: "sql_tcp_address_failed",
        errno.EINVAL: "sql_tcp_address_failed",
        errno.EPROTONOSUPPORT: "sql_tcp_address_failed",
        errno.ECONNABORTED: "sql_tcp_connection_reset",
        errno.ECONNRESET: "sql_tcp_connection_reset",
        errno.EPIPE: "sql_tcp_connection_reset",
    }
    error_number = getattr(error, "errno", None)
    if error_number in categories:
        return categories[error_number]
    error_name = errno.errorcode.get(error_number)
    return f"sql_tcp_os_error_{error_name.casefold()}" if error_name else "sql_tcp_os_error_unknown"


def connect_ipv4(address: tuple[str, int], *, timeout: int) -> socket.socket:
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        connection.connect(address)
    except BaseException:
        connection.close()
        raise
    return connection


def require_tcp_connectivity(
    address: str,
    *,
    attempts: int = PROBE_ATTEMPTS,
    timeout_seconds: int = PROBE_TIMEOUT_SECONDS,
    retry_seconds: int = PROBE_RETRY_SECONDS,
    connect: Callable[..., Any] = connect_ipv4,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if not is_rfc1918(address) or attempts < 1 or timeout_seconds < 1 or retry_seconds < 0:
        raise ConnectivityProbeError("probe_configuration_failed")
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            connection = connect((address, 1433), timeout=timeout_seconds)
            connection.close()
            return
        except OSError as error:
            last_error = error
            if attempt + 1 < attempts:
                sleep(retry_seconds)
    raise ConnectivityProbeError(tcp_failure_category(last_error)) from last_error


def main() -> int:
    try:
        address = resolve_private_address(os.environ.get("LEARNINGNEMO_SQL_SERVER", ""))
        print("PASS sql_private_dns")
        require_tcp_connectivity(address)
        print("PASS sql_private_tcp")
        return 0
    except ConnectivityProbeError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())