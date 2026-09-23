#!/usr/bin/env python3
"""Print redacted Docker step and error lines from the latest ACR QuickRun."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


KEEP = re.compile(
    r"Step\s+\d+|FROM\s|COPY\s|RUN\s|error|failed|not found|no such file|"
    r"checksum|traceback|exception|status:",
    re.IGNORECASE,
)
REDACTIONS = (
    (re.compile(r"https?://\S+", re.IGNORECASE), "<URL>"),
    (re.compile(r"/subscriptions/\S+", re.IGNORECASE), "<ARM-ID>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", re.IGNORECASE), "<ID>"),
    (re.compile(r"sha256:[0-9a-f]{64}", re.IGNORECASE), "<DIGEST>"),
    (re.compile(r"\b[a-z0-9]{5,50}\.azurecr\.io\b", re.IGNORECASE), "<REGISTRY>"),
    (re.compile(r"\b(?:run id|runid)\s*[:=]\s*\S+", re.IGNORECASE), "run=<REDACTED>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}T\S+\b"), "<TIME>"),
)


class AcrBuildSummaryError(RuntimeError):
    pass


def run_json(arguments: list[str]) -> Any:
    result = subprocess.run(
        ["az", *arguments, "--output", "json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise AcrBuildSummaryError("ACR build status query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise AcrBuildSummaryError("ACR build status is unreadable") from error


def sanitize(line: str, registry_name: str) -> str:
    result = line.replace(registry_name, "<REGISTRY>")
    for pattern, replacement in REDACTIONS:
        result = pattern.sub(replacement, result)
    return re.sub(r"\s+", " ", result).strip()[:600]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-state", type=Path, required=True)
    args = parser.parse_args()
    try:
        try:
            state = json.loads(args.artifact_state.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AcrBuildSummaryError("owner-only artifact state is unreadable") from error
        registry_name = state.get("registryName") if isinstance(state, dict) else None
        if not isinstance(registry_name, str) or not registry_name:
            raise AcrBuildSummaryError("owner-only artifact state is invalid")
        runs = run_json(["acr", "task", "list-runs", "--registry", registry_name, "--top", "20"])
        quick_runs = [item for item in runs if isinstance(item, dict) and not item.get("taskName")]
        if not quick_runs:
            raise AcrBuildSummaryError("no ACR QuickRun exists")
        quick_runs.sort(key=lambda item: str(item.get("startTime") or ""), reverse=True)
        latest = quick_runs[0]
        run_id = latest.get("runId")
        status = latest.get("status")
        if not isinstance(run_id, str) or not isinstance(status, str):
            raise AcrBuildSummaryError("latest ACR QuickRun fields differ")
        result = subprocess.run(
            ["az", "acr", "task", "logs", "--registry", registry_name, "--run-id", run_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise AcrBuildSummaryError("ACR build log query failed")
        lines = [sanitize(line, registry_name) for line in result.stdout.splitlines() if KEEP.search(line)]
        print(f"status={status}")
        for line in lines[-20:]:
            print(line)
        return 0
    except AcrBuildSummaryError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())