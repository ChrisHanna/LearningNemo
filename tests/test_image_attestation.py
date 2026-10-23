"""Tests for multi-image SBOM, Grype scan, and Cosign signature supply-chain attestation."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).parents[1]
PHASE_DIR = ROOT / 'infra' / 'next-phase'
if str(PHASE_DIR) not in sys.path:
    sys.path.insert(0, str(PHASE_DIR))

import image_attestation
from trusted_image_release import TrustedImageReleaseError


@pytest.mark.parametrize(
    'repository',
    (
        'trusted-runtime',
        'cloud-console',
        'cloud-agent',
        'cloud-human',
        'invoice-agent',
        'invoice-services',
    ),
)
def test_image_attestation_binds_sbom_scan_signature_and_source_revision(tmp_path: Path, repository: str):
    image_file = tmp_path / 'image.txt'
    sbom_file = tmp_path / 'sbom.json'
    scan_file = tmp_path / 'scan.json'
    sig_file = tmp_path / 'signature.json'
    out_file = tmp_path / 'release.json'

    image_ref = f"crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/{repository}@sha256:{'a' * 64}"
    image_file.write_text(image_ref + '\n', encoding='utf-8')
    sbom_file.write_text('{"bomFormat": "CycloneDX"}\n', encoding='utf-8')
    scan_file.write_text('{"matches": []}\n', encoding='utf-8')
    sig_file.write_text(
        json.dumps(
            {
                'schemaVersion': 1,
                'verified': True,
                'scheme': 'cosign-sign-blob',
                'imageReferenceSha256': hashlib.sha256(image_file.read_bytes()).hexdigest(),
                'publicKeySha256': 'b' * 64,
                'bundle': {'mediaType': 'application/vnd.dev.sigstore.bundle+json;version=0.3'},
            }
        )
        + '\n',
        encoding='utf-8',
    )

    attestation = image_attestation.create_image_attestation(
        image_reference_file=image_file,
        source_revision='c' * 64,
        sbom=sbom_file,
        vulnerability_report=scan_file,
        signature_verification=sig_file,
        output=out_file,
        built_at=datetime(2026, 9, 26, 12, 0, tzinfo=UTC),
    )
    assert attestation['imageReference'] == image_ref
    assert attestation['sourceRevision'] == 'c' * 64
    assert attestation['signatureVerified'] is True
    assert attestation['criticalVulnerabilities'] == 0
    assert attestation['highVulnerabilities'] == 0


def test_image_attestation_rejects_unknown_repo_or_high_cve(tmp_path: Path):
    image_file = tmp_path / 'image.txt'
    sbom_file = tmp_path / 'sbom.json'
    scan_file = tmp_path / 'scan.json'
    sig_file = tmp_path / 'signature.json'
    out_file = tmp_path / 'release.json'

    image_file.write_text(f"example.azurecr.io/learningnemo/unregistered@sha256:{'a' * 64}\n", encoding='utf-8')
    sbom_file.write_text('{"bomFormat": "CycloneDX"}\n', encoding='utf-8')
    scan_file.write_text('{"matches": []}\n', encoding='utf-8')
    sig_file.write_text('{}\n', encoding='utf-8')

    with pytest.raises(TrustedImageReleaseError, match='digest-pinned LearningNeMo repository'):
        image_attestation.create_image_attestation(
            image_reference_file=image_file,
            source_revision='c' * 64,
            sbom=sbom_file,
            vulnerability_report=scan_file,
            signature_verification=sig_file,
            output=out_file,
        )

    image_file.write_text(f"example.azurecr.io/learningnemo/invoice-agent@sha256:{'a' * 64}\n", encoding='utf-8')
    scan_file.write_text(json.dumps({'matches': [{'vulnerability': {'id': 'CVE-1', 'severity': 'Critical'}}]}), encoding='utf-8')
    with pytest.raises(TrustedImageReleaseError, match='high or critical'):
        image_attestation.create_image_attestation(
            image_reference_file=image_file,
            source_revision='c' * 64,
            sbom=sbom_file,
            vulnerability_report=scan_file,
            signature_verification=sig_file,
            output=out_file,
        )


def test_all_build_scripts_wire_supply_chain_attestation():
    helper = (ROOT / 'scripts/attest-container-image.sh').read_text(encoding='utf-8')
    assert 'image_attestation.py' in helper and 'cosign' in helper and 'syft' in helper and 'grype' in helper
    for script_name in ('build-cloud-demo.sh', 'build-invoice-agent.sh', 'build-invoice-services.sh'):
        content = (ROOT / 'scripts' / script_name).read_text(encoding='utf-8')
        assert 'scripts/attest-container-image.sh' in content
