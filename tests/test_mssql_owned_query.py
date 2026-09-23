from __future__ import annotations

import threading

import task_agent.control.mssql_owned_query as owned_query_module
from task_agent.control.mssql_owned_query import MssqlOwnedQueryController


class FakeLifecycleClient:
    def __init__(self) -> None:
        self.calls = []

    async def call(self, procedure, parameters):
        self.calls.append((procedure, dict(parameters)))
        return ()


class BlockingCursor:
    def __init__(self) -> None:
        self.timeout = 0
        self.description = None
        self.statement = None
        self.cancelled = threading.Event()
        self.hstmt = self

    def execute(self, statement):
        self.statement = statement
        self.cancelled.wait(5)

    def _cancel(self):
        self.cancelled.set()

    def close(self):
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = BlockingCursor()
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True
        self.cursor_instance.cancelled.set()


def test_owned_query_default_connection_uses_managed_identity(monkeypatch) -> None:
    captured = {}
    connection = object()

    def fake_connect(connection_string, client_id, **options):
        captured.update(
            connection_string=connection_string,
            client_id=client_id,
            options=options,
        )
        return connection

    monkeypatch.setattr(owned_query_module, "managed_identity_connect", fake_connect)
    controller = MssqlOwnedQueryController(
        lifecycle_client=FakeLifecycleClient(),
        server="sql-example.database.windows.net",
        database="learningnemo",
        client_id="11111111-1111-4111-8111-111111111111",
        owner_instance="replica-1",
        watchdog_seconds=15,
    )

    assert controller._driver_connect() is connection
    assert captured["client_id"] == "11111111-1111-4111-8111-111111111111"
    assert captured["options"] == {
        "timeout_seconds": 15,
        "server": "sql-example.database.windows.net",
    }


async def test_owned_query_registers_runs_and_cancels_only_matching_lease() -> None:
    lifecycle = FakeLifecycleClient()
    connection = FakeConnection()
    controller = MssqlOwnedQueryController(
        lifecycle_client=lifecycle,
        server="sql-example.database.windows.net",
        database="learningnemo",
        client_id="11111111-1111-4111-8111-111111111111",
        owner_instance="replica-1",
        connect=lambda _connection_string: connection,
        watchdog_seconds=15,
    )

    lease_id = await controller.start_owned("run-abcdefgh")
    await controller.cancel_owned("run-abcdefgh", lease_id)

    assert [name for name, _parameters in lifecycle.calls] == [
        "ops.usp_register_query_run",
        "ops.usp_mark_query_running",
    ]
    assert connection.cursor_instance.statement == "EXEC ops.usp_run_controlled_unsafe_query"
    assert connection.cursor_instance.cancelled.is_set()
    assert connection.closed is True


async def test_owned_query_falls_back_to_connection_close_when_cursor_close_fails() -> None:
    lifecycle = FakeLifecycleClient()
    connection = FakeConnection()

    def fail_statement_cancel():
        raise RuntimeError("statement cancel failed")

    connection.cursor_instance._cancel = fail_statement_cancel
    controller = MssqlOwnedQueryController(
        lifecycle_client=lifecycle,
        server="sql-example.database.windows.net",
        database="learningnemo",
        client_id="11111111-1111-4111-8111-111111111111",
        owner_instance="replica-1",
        connect=lambda _connection_string: connection,
        watchdog_seconds=15,
    )

    lease_id = await controller.start_owned("run-abcdefgh")
    await controller.cancel_owned("run-abcdefgh", lease_id)

    assert connection.closed is True
    assert connection.cursor_instance.cancelled.is_set()