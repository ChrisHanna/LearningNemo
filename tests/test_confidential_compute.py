"""Tests for NVIDIA Confidential Computing (NRAS / nvtrust) GPU attestation and KMS Secure Key Release."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from task_agent.control.confidential_compute import (
    AttestationClaims,
    KmsKeyReleasePolicy,
    evaluate_kms_key_release,
    sign_attestation_token,
    verify_attestation_token,
)
from task_agent.control.invoice_governance import DelegationAuthority
from task_agent.control.operations import OperationDeniedError


NONCE = '1' * 64
PCR = '2' * 64


def valid_claims(now: datetime | None = None, **overrides) -> AttestationClaims:
    issued = now or datetime.now(UTC)
    base = {
        'iss': 'https://nras.attestation.nvidia.com',
        'eat_nonce': NONCE,
        'x_nvidia_gpu_arch': 'GH100',
        'x_nvidia_gpu_arch_check': True,
        'x_nvidia_gpu_cc_mode': 'on',
        'x_nvidia_gpu_measurements_matching': True,
        'x_nvidia_gpu_driver_rim_id': 'NV_GPU_DRIVER_GH100_550.90.07',
        'x_nvidia_gpu_vbios_rim_id': 'NV_GPU_VBIOS_GH100_96.00.89.00.01',
        'host_tee_type': 'amd-sev-snp',
        'host_secure_boot': True,
        'host_debug_disabled': True,
        'vtpm_pcr_sha256': PCR,
        'issued_at': issued.isoformat(),
        'expires_at': (issued + timedelta(seconds=180)).isoformat(),
    }
    base.update(overrides)
    return AttestationClaims(**base)


def test_nras_eat_token_verifies_valid_h100_and_b200_confidential_compute_claims():
    nras_key = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    for arch, tee, iss in (
        ('GH100', 'amd-sev-snp', 'https://nras.attestation.nvidia.com'),
        ('GB200', 'intel-tdx', 'https://nvtrust.local.internal'),
    ):
        claims = valid_claims(now, x_nvidia_gpu_arch=arch, host_tee_type=tee, iss=iss)
        signed = sign_attestation_token(claims, nras_key)
        verified = verify_attestation_token(
            signed,
            expected_nonce=NONCE,
            expected_public_key=signed.public_key,
            now=now,
        )
        assert verified == claims


@pytest.mark.parametrize(
    'override,match',
    (
        ({'x_nvidia_gpu_cc_mode': 'off'}, 'Confidential Computing mode must be on'),
        ({'x_nvidia_gpu_cc_mode': 'devtools'}, 'Confidential Computing mode must be on'),
        ({'x_nvidia_gpu_measurements_matching': False}, 'RIM measurements do not match'),
        ({'x_nvidia_gpu_arch_check': False}, 'architecture check failed'),
        ({'host_secure_boot': False}, 'secure boot and debug disabled'),
        ({'host_debug_disabled': False}, 'secure boot and debug disabled'),
        ({'iss': 'https://untrusted.example.com'}, 'untrusted attestation token issuer'),
        ({'eat_nonce': 'f' * 64}, 'eat_nonce differs'),
    ),
)
def test_nras_eat_token_fails_closed_on_unsafe_gpu_or_cvm_posture(override: dict, match: str):
    nras_key = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    signed = sign_attestation_token(valid_claims(now, **override), nras_key)
    with pytest.raises(OperationDeniedError, match=match):
        verify_attestation_token(signed, expected_nonce=NONCE, expected_public_key=signed.public_key, now=now)


def test_kms_secure_key_release_binds_attestation_and_signed_delegation():
    nras_key = Ed25519PrivateKey.generate()
    authority = DelegationAuthority()
    now = datetime.now(UTC)
    attestation = sign_attestation_token(valid_claims(now), nras_key)
    delegation = authority.issue(
        kind='planning',
        run_id='a' * 32,
        sponsor_hash='b' * 64,
        sandbox_id='c' * 32,
        target_id='d' * 32,
        policy_hash='e' * 64,
        capability_hash='f' * 64,
        attestation_hash=attestation.attestation_hash,
        issued_at=now,
        expires_at=now + timedelta(minutes=8),
    )
    policy = KmsKeyReleasePolicy(
        key_id='kv://kvnemo8370187d/keys/isv-model-weights-kek/v1',
        allowed_vtpm_pcrs=(PCR,),
    )

    receipt = evaluate_kms_key_release(
        policy=policy,
        attestation=attestation,
        delegation=delegation,
        expected_nonce=NONCE,
        attestation_public_key=attestation.public_key,
        delegation_public_key=authority.public_key_pem,
        now=now,
    )
    assert receipt.released is True
    assert receipt.attestation_hash == attestation.attestation_hash
    assert receipt.delegation_hash == delegation.delegation_hash
    assert receipt.gpu_arch == 'GH100' and receipt.host_tee_type == 'amd-sev-snp'

    wrong_pcr_policy = policy.model_copy(update={'allowed_vtpm_pcrs': ('9' * 64,)})
    with pytest.raises(OperationDeniedError, match='vTPM PCR measurement'):
        evaluate_kms_key_release(
            policy=wrong_pcr_policy,
            attestation=attestation,
            delegation=delegation,
            expected_nonce=NONCE,
            now=now,
        )

    other_attestation = sign_attestation_token(valid_claims(now, eat_nonce='8' * 64), nras_key)
    with pytest.raises(OperationDeniedError, match='delegation attestation binding differs'):
        evaluate_kms_key_release(
            policy=policy,
            attestation=other_attestation,
            delegation=delegation,
            expected_nonce='8' * 64,
            now=now,
        )
