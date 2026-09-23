#!/usr/bin/env python3
"""Verify sanitized migration evidence after one-shot compute is removed."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

from migration_parameters import MigrationParameterError
from migration_parameters import load_config
from workload_parameters import WorkloadParameterError
from workload_parameters import validate_image_reference
from workload_release import ReleaseEvidenceError
from workload_release import load_release


EXPECTED_FIELDS = {
    "schemaVersion",
    "appliedAt",
    "configuration",
    "migrationBundleSha256",
    "imageDigest",
    "sourceRevision",
    "executionStatus",
    "receiptCount",
    "evidenceSha256",
}
EXPECTED_EVIDENCE = {
    "compiledTemplate",
    "privateParameters",
    "releaseAttestation",
    "executionResult",
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
UUID_SEARCH = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)


class MigrationEvidenceError(RuntimeError):
    pass


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MigrationEvidenceError(f"unable to read {label}") from error
    if not isinstance(value, dict):
        raise MigrationEvidenceError(f"{label} must contain an object")
    return value


def verify(
    manifest_path: Path,
    config_path: Path,
    release_path: Path,
    image_reference_path: Path,
) -> None:
    manifest = read_json(manifest_path, "migration evidence")
    if set(manifest) != EXPECTED_FIELDS or manifest.get("schemaVersion") != 1:
        raise MigrationEvidenceError("migration evidence fields differ")
    try:
        config = load_config(config_path)
    except MigrationParameterError as error:
        raise MigrationEvidenceError(str(error)) from error
    if manifest.get("configuration") != config:
        raise MigrationEvidenceError("migration evidence configuration differs")
    try:
        image_reference = validate_image_reference(image_reference_path.read_text(encoding="utf-8").strip())
        release = load_release(release_path, image_reference)
    except (OSError, WorkloadParameterError, ReleaseEvidenceError) as error:
        raise MigrationEvidenceError("migration release binding differs") from error
    if (
        manifest.get("executionStatus") != "Succeeded"
        or manifest.get("receiptCount") != 6
        or manifest.get("sourceRevision") != release["sourceRevision"]
        or manifest.get("imageDigest") != image_reference.rsplit("@sha256:", 1)[1]
        or SHA256.fullmatch(str(manifest.get("migrationBundleSha256", ""))) is None
    ):
        raise MigrationEvidenceError("migration receipt or image binding differs")
    evidence = manifest.get("evidenceSha256")
    if (
        not isinstance(evidence, dict)
        or set(evidence) != EXPECTED_EVIDENCE
        or any(SHA256.fullmatch(str(value)) is None for value in evidence.values())
    ):
        raise MigrationEvidenceError("migration evidence hashes differ")
    try:
        applied_at = dt.datetime.fromisoformat(str(manifest["appliedAt"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise MigrationEvidenceError("migration applied time is invalid") from error
    if applied_at.utcoffset() != dt.timedelta(0):
        raise MigrationEvidenceError("migration applied time must use UTC")
    serialized = json.dumps(manifest, sort_keys=True)
    if UUID_SEARCH.search(serialized) or "/subscriptions/" in serialized.casefold():
        raise MigrationEvidenceError("migration evidence contains an Azure identifier")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--release-attestation", type=Path, required=True)
    parser.add_argument("--image-reference-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        verify(args.manifest, args.config, args.release_attestation, args.image_reference_file)
        print("PASS sanitized evidence binds six SQL receipts to the signed image release")
        return 0
    except MigrationEvidenceError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())