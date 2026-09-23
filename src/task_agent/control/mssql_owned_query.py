"""Process-local ownership and cancellation for the fixed recursive demo query."""

from __future__ import annotations

import asyncio
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any

from task_agent.control.mssql_client import build_connection_string
from task_agent.control.mssql_client import managed_identity_connect
from task_agent.control.operations import OperationDeniedError
from task_agent.control.sql_backend import SqlProcedureClient
from task_agent.control.sql_backend import SqlProcedureUnavailableError


RUN_ID = re.compile(r"^run-[a-z0-9]{8,64}$")
INSTANCE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
UNSAFE_STATEMENT = "EXEC ops.usp_run_controlled_unsafe_query"


@dataclass
class _OwnedQuery:
    run_id: str
    lease_id: str
    ready: threading.Event
    begin: threading.Event
    executing: threading.Event
    done: threading.Event
    cancelled: threading.Event
    connection: Any = None
    cursor: Any = None
    error: BaseException | None = None
    monitor: asyncio.Task[None] | None = None
    watchdog: asyncio.Task[None] | None = None


class MssqlOwnedQueryController:
    """Own one cursor per run and never accept caller-provided SQL or session IDs."""

    def __init__(
        self,
        *,
        lifecycle_client: SqlProcedureClient,
        server: str,
        database: str,
        client_id: str,
        owner_instance: str,
        connect: Any = None,
        watchdog_seconds: int = 45,
    ) -> None:
        if INSTANCE_ID.fullmatch(owner_instance) is None:
            raise ValueError("query owner instance is invalid")
        if not 15 <= watchdog_seconds <= 120:
            raise ValueError("query watchdog must be between 15 and 120 seconds")
        self._lifecycle = lifecycle_client
        self._connection_string = build_connection_string(
            server=server,
            database=database,
            client_id=client_id,
            application_name="LearningNeMoQueryRunner",
        )
        self._server = server
        self._client_id = client_id
        self._owner_instance = owner_instance
        self._connect = connect
        self._watchdog_seconds = watchdog_seconds
        self._handles: dict[str, _OwnedQuery] = {}
        self._lock = threading.Lock()

    def _driver_connect(self) -> Any:
        if self._connect is not None:
            return self._connect(self._connection_string)
        return managed_identity_connect(
            self._connection_string,
            self._client_id,
            timeout_seconds=15,
            server=self._server,
        )

    @staticmethod
    def _cancel_cursor(cursor: Any) -> None:
        # mssql-python 1.15 exposes cross-thread ODBC SQLCancel on its pinned statement handle.
        statement_handle = getattr(cursor, "hstmt", None)
        cancel = getattr(statement_handle, "_cancel", None)
        if not callable(cancel):
            raise RuntimeError("SQLCancel is unavailable")
        cancel()

    def _run_query(self, handle: _OwnedQuery) -> None:
        try:
            handle.connection = self._driver_connect()
            handle.cursor = handle.connection.cursor()
            if hasattr(handle.cursor, "timeout"):
                handle.cursor.timeout = self._watchdog_seconds + 15
            handle.ready.set()
            handle.begin.wait()
            if not handle.cancelled.is_set():
                handle.executing.set()
                handle.cursor.execute(UNSAFE_STATEMENT)
                if handle.cursor.description:
                    handle.cursor.fetchall()
        except BaseException as error:
            handle.error = error
            handle.ready.set()
        finally:
            try:
                if handle.cursor is not None:
                    try:
                        handle.cursor.close()
                    except Exception as error:
                        if handle.error is None and not handle.cancelled.is_set():
                            handle.error = error
            finally:
                if handle.connection is not None:
                    try:
                        handle.connection.close()
                    except Exception as error:
                        if handle.error is None and not handle.cancelled.is_set():
                            handle.error = error
                handle.done.set()

    async def start_owned(self, run_id: str) -> str:
        if RUN_ID.fullmatch(run_id) is None:
            raise OperationDeniedError("query run identifier is invalid")
        with self._lock:
            if run_id in self._handles:
                raise OperationDeniedError("query run is already owned by this instance")
        lease_id = f"lease-{uuid.uuid4().hex}"
        deadline = datetime.now(UTC) + timedelta(seconds=self._watchdog_seconds)
        await self._lifecycle.call(
            "ops.usp_register_query_run",
            {
                "run_id": run_id,
                "lease_id": lease_id,
                "owner_instance": self._owner_instance,
                "watchdog_deadline": deadline,
            },
        )
        handle = _OwnedQuery(
            run_id=run_id,
            lease_id=lease_id,
            ready=threading.Event(),
            begin=threading.Event(),
            executing=threading.Event(),
            done=threading.Event(),
            cancelled=threading.Event(),
        )
        thread = threading.Thread(target=self._run_query, args=(handle,), daemon=True)
        thread.start()
        opened = await asyncio.to_thread(handle.ready.wait, 10)
        if not opened or handle.error is not None or handle.cursor is None:
            handle.cancelled.set()
            handle.begin.set()
            await self._mark_failed(run_id, lease_id, "query_connection_failed")
            raise SqlProcedureUnavailableError("owned query connection failed")
        with self._lock:
            self._handles[run_id] = handle
        try:
            await self._lifecycle.call(
                "ops.usp_mark_query_running",
                {"run_id": run_id, "lease_id": lease_id},
            )
        except BaseException:
            handle.cancelled.set()
            handle.begin.set()
            await self._mark_failed(run_id, lease_id, "query_start_failed")
            with self._lock:
                self._handles.pop(run_id, None)
            raise
        handle.monitor = asyncio.create_task(self._monitor(handle))
        handle.watchdog = asyncio.create_task(self._watchdog(handle))
        handle.begin.set()
        executing = await asyncio.to_thread(handle.executing.wait, 5)
        if not executing:
            await self.cancel_owned(run_id, lease_id)
            await self._mark_failed(run_id, lease_id, "query_execution_failed")
            raise SqlProcedureUnavailableError("owned query did not begin")
        return lease_id

    async def _mark_failed(self, run_id: str, lease_id: str, code: str) -> None:
        try:
            await self._lifecycle.call(
                "ops.usp_mark_query_failed",
                {"run_id": run_id, "lease_id": lease_id, "failure_code": code},
            )
        except SqlProcedureUnavailableError:
            return

    async def _monitor(self, handle: _OwnedQuery) -> None:
        await asyncio.to_thread(handle.done.wait)
        if not handle.cancelled.is_set():
            await self._mark_failed(handle.run_id, handle.lease_id, "unsafe_query_ended")
        with self._lock:
            if self._handles.get(handle.run_id) is handle:
                self._handles.pop(handle.run_id, None)

    async def _watchdog(self, handle: _OwnedQuery) -> None:
        await asyncio.sleep(self._watchdog_seconds)
        with self._lock:
            active = self._handles.get(handle.run_id) is handle
        if active and not handle.done.is_set():
            await self.cancel_owned(handle.run_id, handle.lease_id)
            await self._mark_failed(handle.run_id, handle.lease_id, "watchdog_expired")

    async def cancel_owned(self, run_id: str, lease_id: str) -> None:
        with self._lock:
            handle = self._handles.get(run_id)
        if handle is None or handle.lease_id != lease_id:
            raise OperationDeniedError("query connection lease is not owned by this instance")
        handle.cancelled.set()
        if handle.watchdog is not None and handle.watchdog is not asyncio.current_task():
            handle.watchdog.cancel()
        if handle.cursor is not None and not handle.done.is_set():
            try:
                await asyncio.to_thread(self._cancel_cursor, handle.cursor)
            except Exception as error:
                try:
                    await asyncio.to_thread(handle.connection.close)
                except Exception as close_error:
                    raise SqlProcedureUnavailableError("owned query cancellation failed") from close_error
        completed = await asyncio.to_thread(handle.done.wait, 10)
        if not completed:
            try:
                await asyncio.to_thread(handle.connection.close)
            except Exception as error:
                raise SqlProcedureUnavailableError("owned query connection close failed") from error
            completed = await asyncio.to_thread(handle.done.wait, 5)
        if not completed:
            raise SqlProcedureUnavailableError("owned query did not stop before the deadline")
        with self._lock:
            if self._handles.get(run_id) is handle:
                self._handles.pop(run_id, None)

    async def close(self) -> None:
        with self._lock:
            owned = tuple((handle.run_id, handle.lease_id) for handle in self._handles.values())
        for run_id, lease_id in owned:
            try:
                await self.cancel_owned(run_id, lease_id)
            except (OperationDeniedError, SqlProcedureUnavailableError):
                continue