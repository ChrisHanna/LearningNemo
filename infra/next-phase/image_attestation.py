#!/usr/bin/env python3
"""Verify and emit hash-bound SBOM, Grype scan, and Cosign signature attestations for any LearningNeMo image."""

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

from trusted_image_release import TrustedImageReleaseError, validate_signature, vulnerability_counts


IMAGE_REFERENCE = re.compile(
    r"^[a-z0-9][a-z0-9._-]*(?:\.[a-z0-9][a-z0-9._-]*)+/learningnemo/"
    r"(trusted-runtime|cloud-console|cloud-agent|cloud-human|invoice-agent|invoice-services)"
    r"@sha256:[0-9a-f]{64}$"
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_image_attestation(
    *,
    image_reference_file: Path,
    source_revision: str,
    sbom: Path,
    vulnerability_report: Path,
    signature_verification: Path,
    output: Path,
    built_at: dt.datetime | None = None,
) -> dict[str, Any]:
    try:
        image_reference = image_reference_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise TrustedImageReleaseError("image reference file is unreadable") from error
    if IMAGE_REFERENCE.fullmatch(image_reference) is None:
        raise TrustedImageReleaseError("image reference must be a digest-pinned LearningNeMo repository reference")
    if SHA256.fullmatch(source_revision) is None:
        raise TrustedImageReleaseError("source revision must be a 64-character lowercase hex SHA-256 digest")
    try:
        sbom_doc = json.loads(sbom.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TrustedImageReleaseError("SBOM artifact is unreadable or invalid JSON") from error
    if not isinstance(sbom_doc, dict) or sbom_doc.get("bomFormat") != "CycloneDX":
        raise TrustedImageReleaseError("SBOM artifact must be a CycloneDX JSON document")
    critical, high = vulnerability_counts(vulnerability_report)
    if critical or high:
        raise TrustedImageReleaseError("container image contains a high or critical vulnerability")
    validate_signature(signature_verification, image_reference_file)
    current = (built_at or dt.datetime.now(dt.UTC)).astimezone(dt.UTC).replace(microsecond=0)
    attestation = {
        "schemaVersion": 1,
        "imageReference": image_reference,
        "sourceRevision": source_revision,
        "builtAt": current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sbomSha256": file_sha256(sbom),
        "vulnerabilityScanSha256": file_sha256(vulnerability_report),
        "signatureVerificationSha256": file_sha256(signature_verification),
        "signatureVerified": True,
        "criticalVulnerabilities": critical,
        "highVulnerabilities": high,
    }
    output.write_text(json.dumps(attestation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)
    return attestation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-reference-file", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--vulnerability-report", type=Path, required=True)
    parser.add_argument("--signature-verification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        create_image_attestation(
            image_reference_file=args.image_reference_file,
            source_revision=args.source_revision,
            sbom=args.sbom,
            vulnerability_report=args.vulnerability_report,
            signature_verification=args.signature_verification,
            output=args.output,
        )
        print("PASS generated supply-chain attestation from SBOM, scan, and Cosign signature verification")
        return 0
    except TrustedImageReleaseError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
