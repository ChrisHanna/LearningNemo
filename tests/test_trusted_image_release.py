from __future__ import annotations

import json
import hashlib
import stat
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
PHASE_DIR = ROOT / "infra" / "next-phase"
sys.path.insert(0, str(PHASE_DIR))

import trusted_image_release
import verify_image_elf


def release_files(tmp_path: Path) -> dict[str, Path]:
    files = {
        "image": tmp_path / "image.txt",
        "sbom": tmp_path / "sbom.json",
        "scan": tmp_path / "scan.json",
        "signature": tmp_path / "signature.json",
        "schema": tmp_path / "schema.sql",
        "release": tmp_path / "release.json",
    }
    files["image"].write_text(f"example.azurecr.io/learningnemo/trusted-runtime@sha256:{'a' * 64}\n")
    files["sbom"].write_text('{"bomFormat":"CycloneDX"}\n')
    files["scan"].write_text('{"matches":[]}\n')
    files["signature"].write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "verified": True,
                "scheme": "cosign-sign-blob",
                "imageReferenceSha256": hashlib.sha256(files["image"].read_bytes()).hexdigest(),
                "publicKeySha256": "b" * 64,
                "bundle": {"mediaType": "application/vnd.dev.sigstore.bundle+json;version=0.3"},
            }
        )
        + "\n"
    )
    files["schema"].write_text("CREATE TABLE control.Approvals (ApprovalId int);\n")
    return files


def test_release_evidence_hashes_exact_source_and_artifacts(tmp_path: Path) -> None:
    files = release_files(tmp_path)

    release = trusted_image_release.create_release(
        root=ROOT,
        image_reference_file=files["image"],
        sbom=files["sbom"],
        vulnerability_report=files["scan"],
        signature_verification=files["signature"],
        approval_schema=files["schema"],
        output=files["release"],
        built_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
    )

    assert release["sourceRevision"] == trusted_image_release.source_revision(ROOT)
    assert release["criticalVulnerabilities"] == 0
    assert release["highVulnerabilities"] == 0
    assert stat.S_IMODE(files["release"].stat().st_mode) == 0o600


def test_release_rejects_high_vulnerability_or_empty_signature(tmp_path: Path) -> None:
    files = release_files(tmp_path)
    scan = {
        "matches": [
            {"vulnerability": {"id": "CVE-example", "severity": "High"}},
        ]
    }
    files["scan"].write_text(json.dumps(scan))
    with pytest.raises(trusted_image_release.TrustedImageReleaseError, match="high or critical"):
        trusted_image_release.create_release(
            root=ROOT,
            image_reference_file=files["image"],
            sbom=files["sbom"],
            vulnerability_report=files["scan"],
            signature_verification=files["signature"],
            approval_schema=files["schema"],
            output=files["release"],
        )

    files["scan"].write_text('{"matches":[]}')
    files["signature"].write_text("{}")
    with pytest.raises(trusted_image_release.TrustedImageReleaseError, match="no verified signature"):
        trusted_image_release.validate_signature(files["signature"])


def test_source_revision_changes_with_source_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")
    monkeypatch.setattr(trusted_image_release, "source_files", lambda _root: (first, second))
    before = trusted_image_release.source_revision(tmp_path)

    second.write_text("changed\n", encoding="utf-8")

    assert trusted_image_release.source_revision(tmp_path) != before


def test_elf_needed_parser_accepts_readelf_format() -> None:
    output = " 0x0000000000000001 (NEEDED) Shared library: [libssl.so.3]\n"
    assert verify_image_elf.NEEDED.findall(output) == ["libssl.so.3"]