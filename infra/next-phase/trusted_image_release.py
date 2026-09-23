#!/usr/bin/env python3
"""Create truthful, hash-bound release evidence for the trusted runtime image."""

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
from workload_release import sha256


SOURCE_FILES = (
    ".dockerignore",
    "containers/trusted-runtime.Dockerfile",
    "containers/trusted-runtime.base-images.json",
    "containers/trusted-runtime.requirements.in",
    "containers/trusted-runtime.requirements.lock",
    "containers/zlib-ng-2.3.3.spdx.json",
    "scripts/apply-sql-migrations.py",
    "scripts/extract-runtime-library.py",
    "scripts/extract-zlib-ng-source.py",
    "scripts/prepare-trusted-runtime.py",
    "scripts/probe-sql-connectivity.py",
    "scripts/probe-sql-odbc.py",
    "scripts/run-live-cycle-workflow.py",
    "scripts/verify-runtime-native.py",
    "infra/next-phase/verify_image_elf.py",
    "infra/next-phase/sql/005_control_workflow.sql",
    "infra/next-phase/sql/006_reconcile_expired_query_runs.sql",
    "src/task_agent/__init__.py",
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class TrustedImageReleaseError(RuntimeError):
    pass


def source_files(root: Path) -> tuple[Path, ...]:
    fixed = [root / name for name in SOURCE_FILES]
    control = sorted((root / "src" / "task_agent" / "control").glob("**/*.py"))
    files = tuple([*fixed, *control])
    if not files or any(not path.is_file() for path in files):
        raise TrustedImageReleaseError("trusted image source inventory is incomplete")
    return files


def source_revision(root: Path) -> str:
    digest = hashlib.sha256()
    for path in source_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def vulnerability_counts(path: Path) -> tuple[int, int]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TrustedImageReleaseError("vulnerability report is unreadable") from error
    matches = report.get("matches") if isinstance(report, dict) else None
    if not isinstance(matches, list):
        raise TrustedImageReleaseError("vulnerability report shape differs")
    severities = [
        str((item.get("vulnerability") or {}).get("severity", "")).casefold()
        for item in matches
        if isinstance(item, dict)
    ]
    return severities.count("critical"), severities.count("high")


def validate_signature(path: Path, image_reference_file: Path | None = None) -> None:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TrustedImageReleaseError("signature verification report is unreadable") from error
    if (
        not isinstance(report, dict)
        or set(report)
        != {
            "schemaVersion",
            "verified",
            "scheme",
            "imageReferenceSha256",
            "publicKeySha256",
            "bundle",
        }
        or report.get("schemaVersion") != 1
        or report.get("verified") is not True
        or report.get("scheme") != "cosign-sign-blob"
        or SHA256.fullmatch(str(report.get("imageReferenceSha256", ""))) is None
        or SHA256.fullmatch(str(report.get("publicKeySha256", ""))) is None
        or not isinstance(report.get("bundle"), dict)
    ):
        raise TrustedImageReleaseError("signature verification report contains no verified signature")
    if image_reference_file is not None and report["imageReferenceSha256"] != sha256(image_reference_file):
        raise TrustedImageReleaseError("signature verification does not bind the image reference")


def create_release(
    *,
    root: Path,
    image_reference_file: Path,
    sbom: Path,
    vulnerability_report: Path,
    signature_verification: Path,
    approval_schema: Path,
    output: Path,
    built_at: dt.datetime | None = None,
) -> dict[str, Any]:
    try:
        image_reference = validate_image_reference(image_reference_file.read_text(encoding="utf-8").strip())
    except (OSError, WorkloadParameterError) as error:
        raise TrustedImageReleaseError("image reference is unreadable or invalid") from error
    critical, high = vulnerability_counts(vulnerability_report)
    if critical or high:
        raise TrustedImageReleaseError("trusted image contains a high or critical vulnerability")
    validate_signature(signature_verification, image_reference_file)
    current = (built_at or dt.datetime.now(dt.UTC)).astimezone(dt.UTC).replace(microsecond=0)
    release = {
        "schemaVersion": 1,
        "imageReference": image_reference,
        "sourceRevision": source_revision(root),
        "builtAt": current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sbomSha256": sha256(sbom),
        "vulnerabilityScanSha256": sha256(vulnerability_report),
        "signatureVerificationSha256": sha256(signature_verification),
        "signatureVerified": True,
        "criticalVulnerabilities": critical,
        "highVulnerabilities": high,
        "approvalAuthority": "azure-sql",
        "approvalSchemaSha256": sha256(approval_schema),
    }
    if SHA256.fullmatch(release["sourceRevision"]) is None:
        raise TrustedImageReleaseError("trusted image source revision is invalid")
    output.write_text(json.dumps(release, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)
    return release


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--image-reference-file", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--vulnerability-report", type=Path, required=True)
    parser.add_argument("--signature-verification", type=Path, required=True)
    parser.add_argument("--approval-schema", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        create_release(
            root=args.root,
            image_reference_file=args.image_reference_file,
            sbom=args.sbom,
            vulnerability_report=args.vulnerability_report,
            signature_verification=args.signature_verification,
            approval_schema=args.approval_schema,
            output=args.output,
        )
        print("PASS generated release evidence from source, SBOM, scan, and signature verification")
        return 0
    except TrustedImageReleaseError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())