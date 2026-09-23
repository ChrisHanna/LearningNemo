from __future__ import annotations

import errno
import importlib.util
import socket
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "probe_sql_connectivity",
    ROOT / "scripts" / "probe-sql-connectivity.py",
)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_probe_resolves_one_rfc1918_ipv4_address() -> None:
    address = probe.resolve_private_address(
        "example.database.windows.net",
        resolver=lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.40.1.4", 1433)),
        ],
    )

    assert address == "10.40.1.4"


@pytest.mark.parametrize(
    ("records", "reason"),
    [
        ([], "sql_dns_address_count_invalid"),
        (
            [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.40.1.4", 1433)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.40.1.5", 1433)),
            ],
            "sql_dns_address_count_invalid",
        ),
        ([(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("20.1.2.3", 1433))], "sql_dns_not_private"),
    ],
)
def test_probe_fails_closed_for_unexpected_dns(records, reason) -> None:
    with pytest.raises(probe.ConnectivityProbeError, match=reason):
        probe.resolve_private_address(
            "example.database.windows.net",
            resolver=lambda *_args, **_kwargs: records,
        )


def test_probe_retries_tcp_and_closes_successful_socket() -> None:
    events = []

    class FakeSocket:
        def close(self):
            events.append("close")

    outcomes = iter([TimeoutError(), FakeSocket()])

    def connect(*_args, **_kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, OSError):
            raise outcome
        return outcome

    probe.require_tcp_connectivity(
        "10.40.1.4",
        attempts=2,
        connect=connect,
        sleep=lambda seconds: events.append(("sleep", seconds)),
    )

    assert events == [("sleep", 5), "close"]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TimeoutError(), "sql_tcp_connect_timed_out"),
        (PermissionError(errno.EACCES, "sensitive"), "sql_tcp_connect_blocked"),
        (OSError(errno.EINPROGRESS, "sensitive"), "sql_tcp_os_error_einprogress"),
        (OSError(None, "sensitive"), "sql_tcp_os_error_unknown"),
    ],
)
def test_probe_reports_only_safe_tcp_category(error, reason) -> None:
    with pytest.raises(probe.ConnectivityProbeError, match=f"^{reason}$"):
        probe.require_tcp_connectivity(
            "10.40.1.4",
            attempts=1,
            connect=lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
        )