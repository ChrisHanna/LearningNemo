#!/usr/bin/env python3
"""Record identifier-free evidence from one completed live control cycle."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from workload_parameters import WorkloadParameterError
from workload_parameters import validate_image_reference
from workload_release import ReleaseEvidenceError
from workload_release import load_release


EXPECTED_STAGES = {
    "live_workflow_workers_ready",
    "live_workflow_initialized",
    "live_workflow_diagnosed",
    "live_workflow_contained",
    "live_workflow_approved",
    "live_workflow_remediated",
    "live_workflow_verified",
    "live_workflow_completed",
}
RESULT_PATTERN = re.compile(r"\b(PASS|FAIL) ([a-z0-9_]+)\b")
SAFE_FAILURES = {
    "live_workflow_failed",
    "worker_not_ready",
    "worker_request_failed",
    "worker_response_invalid",
    "worker_response_too_large",
    "worker_token_failed",
    "workflow_audience_configuration_failed",
    "workflow_configuration_failed",
    "workflow_containment_failed",
    "workflow_diagnosis_failed",
    "workflow_endpoint_configuration_failed",
    "workflow_initialize_failed",
    "workflow_plan_failed",
    "workflow_query_start_failed",
    "workflow_remediation_failed",
    "workflow_separation_of_duties_failed",
    "workflow_subject_hash_failed",
    "workflow_summary_failed",
    "workflow_verification_failed",
}
HTTP_FAILURE_PATTERN = re.compile(r"^worker_http_(?:4[0-9]{2}|5[0-9]{2})$")


class ControlCycleSummaryError(RuntimeError):
    pass


def read_evidence(path: Path) -> Any:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ControlCycleSummaryError("control-cycle evidence is unreadable") from error
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


def observations(execution: Any, logs: Any) -> tuple[str | None, set[str], set[str]]:
    status = (execution.get("properties") or {}).get("status") if isinstance(execution, dict) else None
    results = {
        (match.group(1), match.group(2))
        for text in string_values(logs)
        for match in RESULT_PATTERN.finditer(text)
    }
    passes = {
        category for outcome, category in results
        if outcome == "PASS" and category in EXPECTED_STAGES
    }
    failures = {
        category for outcome, category in results
        if outcome == "FAIL" and (category in SAFE_FAILURES or HTTP_FAILURE_PATTERN.fullmatch(category))
    }
    return status if isinstance(status, str) else None, passes, failures


def summarize(execution: Any, logs: Any) -> dict[str, bool]:
    status, passes, failures = observations(execution, logs)
    if status != "Succeeded" or passes != EXPECTED_STAGES or failures:
        raise ControlCycleSummaryError("live control-cycle evidence contract differs")
    return {stage: True for stage in sorted(EXPECTED_STAGES)}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_report(
    output: Path,
    checks: dict[str, bool],
    release_path: Path,
    image_reference_path: Path,
    template_path: Path,
    parameters_path: Path,
    execution_path: Path,
    *,
    recorded_at: dt.datetime | None = None,
) -> None:
    try:
        image_reference = validate_image_reference(image_reference_path.read_text(encoding="utf-8").strip())
        release = load_release(release_path, image_reference)
    except (OSError, WorkloadParameterError, ReleaseEvidenceError) as error:
        raise ControlCycleSummaryError("control-cycle release evidence differs") from error
    current = (recorded_at or dt.datetime.now(dt.UTC)).astimezone(dt.UTC).replace(microsecond=0)
    report = {
        "schemaVersion": 1,
        "status": "passed",
        "checks": checks,
        "imageDigest": image_reference.rsplit("@sha256:", 1)[1],
        "sourceRevision": release["sourceRevision"],
        "evidenceSha256": {
            "compiledTemplate": sha256(template_path),
            "privateParameters": sha256(parameters_path),
            "releaseAttestation": sha256(release_path),
            "executionResult": sha256(execution_path),
        },
        "recordedAt": current.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--release-attestation", type=Path, required=True)
    parser.add_argument("--image-reference-file", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execution = read_evidence(args.execution)
    logs = read_evidence(args.logs)
    try:
        checks = summarize(execution, logs)
        write_report(
            args.output,
            checks,
            args.release_attestation,
            args.image_reference_file,
            args.template,
            args.parameters,
            args.execution,
        )
    except ControlCycleSummaryError as error:
        status, passes, failures = observations(execution, logs)
        print(f"INFO live control-cycle execution status: {status or 'unavailable'}")
        for stage in sorted(passes):
            print(f"INFO live control-cycle completed stage: {stage}")
        for failure in sorted(failures):
            print(f"INFO live control-cycle failure category: {failure}")
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    print("PASS live control cycle completed all eight approval-bound stages")
    print("PASS wrote owner-only identifier-free control-cycle evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())