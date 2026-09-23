#!/usr/bin/env python3
"""Verify preserved identifier-free evidence from a completed live control cycle."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

from summarize_control_cycle import EXPECTED_STAGES
from workload_parameters import WorkloadParameterError
from workload_parameters import validate_image_reference
from workload_release import ReleaseEvidenceError
from workload_release import load_release


EXPECTED_FIELDS = {
    "schemaVersion",
    "status",
    "checks",
    "imageDigest",
    "sourceRevision",
    "evidenceSha256",
    "recordedAt",
}
EXPECTED_EVIDENCE = {
    "compiledTemplate",
    "privateParameters",
    "releaseAttestation",
    "executionResult",
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REVISION = re.compile(r"^[0-9a-f]{40,64}$")
UUID_SEARCH = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)


class ControlCycleEvidenceError(RuntimeError):
    pass


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ControlCycleEvidenceError(f"unable to read {label}") from error
    if not isinstance(value, dict):
        raise ControlCycleEvidenceError(f"{label} must contain an object")
    return value


def verify(manifest_path: Path, release_path: Path, image_path: Path) -> None:
    manifest = read_json(manifest_path, "control-cycle evidence")
    if set(manifest) != EXPECTED_FIELDS or manifest.get("schemaVersion") != 1:
        raise ControlCycleEvidenceError("control-cycle evidence fields differ")
    checks = manifest.get("checks")
    if (
        manifest.get("status") != "passed"
        or not isinstance(checks, dict)
        or set(checks) != EXPECTED_STAGES
        or any(value is not True for value in checks.values())
    ):
        raise ControlCycleEvidenceError("control-cycle stage evidence differs")
    try:
        image_reference = validate_image_reference(image_path.read_text(encoding="utf-8").strip())
        release = load_release(release_path, image_reference)
    except (OSError, WorkloadParameterError, ReleaseEvidenceError) as error:
        raise ControlCycleEvidenceError("control-cycle release binding differs") from error
    if (
        manifest.get("imageDigest") != image_reference.rsplit("@sha256:", 1)[1]
        or manifest.get("sourceRevision") != release["sourceRevision"]
        or SHA256.fullmatch(str(manifest.get("imageDigest", ""))) is None
        or REVISION.fullmatch(str(manifest.get("sourceRevision", ""))) is None
    ):
        raise ControlCycleEvidenceError("control-cycle image or source binding differs")
    evidence = manifest.get("evidenceSha256")
    if (
        not isinstance(evidence, dict)
        or set(evidence) != EXPECTED_EVIDENCE
        or any(SHA256.fullmatch(str(value)) is None for value in evidence.values())
    ):
        raise ControlCycleEvidenceError("control-cycle evidence hashes differ")
    try:
        recorded_at = dt.datetime.fromisoformat(str(manifest["recordedAt"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise ControlCycleEvidenceError("control-cycle recorded time is invalid") from error
    if recorded_at.utcoffset() != dt.timedelta(0) or recorded_at.microsecond:
        raise ControlCycleEvidenceError("control-cycle recorded time must use whole-second UTC")
    serialized = json.dumps(manifest, sort_keys=True)
    if UUID_SEARCH.search(serialized) or "/subscriptions/" in serialized.casefold():
        raise ControlCycleEvidenceError("control-cycle evidence contains an Azure identifier")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--release-attestation", type=Path, required=True)
    parser.add_argument("--image-reference-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        verify(args.manifest, args.release_attestation, args.image_reference_file)
    except ControlCycleEvidenceError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    print("PASS preserved evidence binds all eight live stages to the current signed release")
    print("PASS preserved control-cycle evidence contains no Azure identifier")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())