#!/usr/bin/env python3
"""Validate private release evidence required before trusted workload deployment."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from workload_parameters import WorkloadParameterError
from workload_parameters import validate_image_reference


EXPECTED_FIELDS = {
    "schemaVersion",
    "imageReference",
    "sourceRevision",
    "builtAt",
    "sbomSha256",
    "vulnerabilityScanSha256",
    "signatureVerificationSha256",
    "signatureVerified",
    "criticalVulnerabilities",
    "highVulnerabilities",
    "approvalAuthority",
    "approvalSchemaSha256",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
ARTIFACT_HASH_FIELDS = {
    "sbom": "sbomSha256",
    "vulnerabilityReport": "vulnerabilityScanSha256",
    "signatureVerification": "signatureVerificationSha256",
    "approvalSchema": "approvalSchemaSha256",
}


class ReleaseEvidenceError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise ReleaseEvidenceError("unable to read a release evidence artifact") from error
    return digest.hexdigest()


def load_release(
    path: Path,
    image_reference: str,
    *,
    artifacts: Mapping[str, Path] | None = None,
    expected_source_revision: str | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseEvidenceError("unable to read workload release evidence") from error
    if not isinstance(document, dict) or set(document) != EXPECTED_FIELDS:
        raise ReleaseEvidenceError("workload release evidence fields differ from the contract")
    try:
        expected_image = validate_image_reference(image_reference)
    except WorkloadParameterError as error:
        raise ReleaseEvidenceError(str(error)) from error
    if document.get("schemaVersion") != 1 or document.get("imageReference") != expected_image:
        raise ReleaseEvidenceError("release evidence does not bind the exact image digest")
    if REVISION_PATTERN.fullmatch(str(document.get("sourceRevision", ""))) is None:
        raise ReleaseEvidenceError("release evidence source revision is invalid")
    if (
        expected_source_revision is not None
        and document["sourceRevision"] != expected_source_revision
    ):
        raise ReleaseEvidenceError("release evidence does not match current trusted image source")
    for name in (
        "sbomSha256",
        "vulnerabilityScanSha256",
        "signatureVerificationSha256",
        "approvalSchemaSha256",
    ):
        if SHA256_PATTERN.fullmatch(str(document.get(name, ""))) is None:
            raise ReleaseEvidenceError(f"release evidence {name} is invalid")
    try:
        built_at = dt.datetime.fromisoformat(str(document["builtAt"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise ReleaseEvidenceError("release evidence builtAt is invalid") from error
    current = now or dt.datetime.now(dt.UTC)
    if (
        built_at.utcoffset() != dt.timedelta(0)
        or built_at.microsecond
        or built_at > current + dt.timedelta(minutes=5)
    ):
        raise ReleaseEvidenceError("release evidence builtAt must be a whole-second UTC timestamp")
    if document.get("signatureVerified") is not True:
        raise ReleaseEvidenceError("image signature verification evidence is required")
    if document.get("criticalVulnerabilities") != 0 or document.get("highVulnerabilities") != 0:
        raise ReleaseEvidenceError("image scan must report zero high and critical vulnerabilities")
    if document.get("approvalAuthority") != "azure-sql":
        raise ReleaseEvidenceError("Azure SQL authoritative approval persistence is required")
    if artifacts is not None:
        if set(artifacts) != set(ARTIFACT_HASH_FIELDS):
            raise ReleaseEvidenceError("release evidence artifact inventory differs")
        for name, hash_field in ARTIFACT_HASH_FIELDS.items():
            if sha256(artifacts[name]) != document[hash_field]:
                raise ReleaseEvidenceError(f"release evidence artifact hash differs: {name}")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--image-reference-file", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--vulnerability-report", type=Path, required=True)
    parser.add_argument("--signature-verification", type=Path, required=True)
    parser.add_argument("--approval-schema", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        try:
            image_reference = args.image_reference_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise ReleaseEvidenceError("unable to read private image reference") from error
        try:
            from trusted_image_release import source_revision

            expected_source_revision = source_revision(args.source_root)
        except (OSError, RuntimeError) as error:
            raise ReleaseEvidenceError("unable to hash current trusted image source") from error
        load_release(
            args.attestation,
            image_reference,
            artifacts={
                "sbom": args.sbom,
                "vulnerabilityReport": args.vulnerability_report,
                "signatureVerification": args.signature_verification,
                "approvalSchema": args.approval_schema,
            },
            expected_source_revision=expected_source_revision,
        )
        print("PASS image digest, SBOM, scan, signature, and approval persistence evidence match")
        return 0
    except ReleaseEvidenceError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())