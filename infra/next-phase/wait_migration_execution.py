#!/usr/bin/env python3
"""Wait for one Container Apps job execution without printing identifiers."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any


TERMINAL_FAILURES = {"Canceled", "Cancelled", "Failed", "Stopped"}


class MigrationWaitError(RuntimeError):
    pass


def execution_status(resource_group: str, job_name: str, execution_name: str) -> str:
    result = subprocess.run(
        [
            "az",
            "containerapp",
            "job",
            "execution",
            "show",
            "--resource-group",
            resource_group,
            "--name",
            job_name,
            "--job-execution-name",
            execution_name,
            "--output",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode != 0:
        raise MigrationWaitError("migration execution status query failed")
    try:
        execution: Any = json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise MigrationWaitError("migration execution status is unreadable") from error
    status = (execution.get("properties") or {}).get("status") or execution.get("status")
    if not isinstance(status, str) or not status:
        raise MigrationWaitError("migration execution status is absent")
    return status


def wait_for_execution(
    get_status: Callable[[], str],
    *,
    timeout_seconds: int,
    interval_seconds: int,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    deadline = monotonic() + timeout_seconds
    while True:
        status = get_status()
        if status == "Succeeded":
            return status
        if status in TERMINAL_FAILURES:
            raise MigrationWaitError(f"migration execution reached terminal status: {status.casefold()}")
        if monotonic() >= deadline:
            raise MigrationWaitError("migration execution wait timed out")
        sleep(interval_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--execution-name", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--interval-seconds", type=int, default=5)
    args = parser.parse_args()
    try:
        if not 60 <= args.timeout_seconds <= 900 or not 2 <= args.interval_seconds <= 30:
            raise MigrationWaitError("migration wait bounds are invalid")
        wait_for_execution(
            lambda: execution_status(args.resource_group, args.job_name, args.execution_name),
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
        )
        print("PASS migration execution reached succeeded status")
        return 0
    except MigrationWaitError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())