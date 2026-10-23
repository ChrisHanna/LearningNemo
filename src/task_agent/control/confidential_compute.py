"""NVIDIA Confidential Computing (NRAS / nvtrust) GPU + CVM attestation and KMS Secure Key Release (SKR)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field

from task_agent.control.canonical import canonical_json, content_hash
from task_agent.control.invoice_contract import Digest, Identifier
from task_agent.control.invoice_governance import WORKSPACE_ID, SignedDelegation, verify_delegation
from task_agent.control.operations import OperationDeniedError


ALLOWED_ISSUERS = frozenset({
    'https://nras.attestation.nvidia.com',
    'https://nvtrust.local.internal',
})


class AttestationClaims(BaseModel):
    """Entity Attestation Token (EAT) claims modeled on NVIDIA NRAS and Confidential VM vTPM evidence."""

    model_config = ConfigDict(extra='forbid', frozen=True)
    version: Literal[1] = 1
    iss: str = Field(min_length=1, max_length=120)
    workspace_id: str = Field(default=WORKSPACE_ID, min_length=1, max_length=80)
    eat_nonce: Digest
    x_nvidia_gpu_arch: Literal['GH100', 'GB200']
    x_nvidia_gpu_arch_check: bool
    x_nvidia_gpu_cc_mode: Literal['on', 'off', 'devtools']
    x_nvidia_gpu_measurements_matching: bool
    x_nvidia_gpu_driver_rim_id: str = Field(min_length=1, max_length=80)
    x_nvidia_gpu_vbios_rim_id: str = Field(min_length=1, max_length=80)
    host_tee_type: Literal['amd-sev-snp', 'intel-tdx']
    host_secure_boot: bool
    host_debug_disabled: bool
    vtpm_pcr_sha256: Digest
    issued_at: str
    expires_at: str


class SignedAttestationToken(BaseModel):
    """Ed25519-signed NRAS/nvtrust Entity Attestation Token."""

    model_config = ConfigDict(extra='forbid', frozen=True)
    claims: AttestationClaims
    attestation_hash: Digest
    key_id: str = Field(min_length=1, max_length=80)
    public_key: str = Field(min_length=32, max_length=256)
    signature: str = Field(min_length=64, max_length=256)


class KmsKeyReleasePolicy(BaseModel):
    """Partner KMS / HSM Secure Key Release (SKR) policy binding key access to GPU + CVM attestation."""

    model_config = ConfigDict(extra='forbid', frozen=True)
    key_id: str = Field(min_length=1, max_length=120)
    allowed_issuers: tuple[str, ...] = tuple(sorted(ALLOWED_ISSUERS))
    allowed_gpu_archs: tuple[Literal['GH100', 'GB200'], ...] = ('GH100', 'GB200')
    allowed_tee_types: tuple[Literal['amd-sev-snp', 'intel-tdx'], ...] = ('amd-sev-snp', 'intel-tdx')
    allowed_vtpm_pcrs: tuple[Digest, ...] = Field(min_length=1)
    require_signed_delegation: bool = True


class KeyReleaseReceipt(BaseModel):
    """Tamper-evident receipt from a KMS Secure Key Release evaluation."""

    model_config = ConfigDict(extra='forbid', frozen=True)
    released: Literal[True] = True
    key_id: str
    workspace_id: str
    run_id: Identifier
    sandbox_id: Identifier
    gpu_arch: Literal['GH100', 'GB200']
    host_tee_type: Literal['amd-sev-snp', 'intel-tdx']
    attestation_hash: Digest
    delegation_hash: Digest
    wrapped_dek_sha256: Digest


def sign_attestation_token(
    claims: AttestationClaims,
    private_key: Ed25519PrivateKey,
    *,
    key_id: str = 'nvidia-nras-ed25519-v1',
) -> SignedAttestationToken:
    """Sign an AttestationClaims payload with an Ed25519 attestation authority key."""
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode('ascii')
    signature = private_key.sign(canonical_json(claims).encode('utf-8'))
    return SignedAttestationToken(
        claims=claims,
        attestation_hash=content_hash(claims),
        key_id=key_id,
        public_key=public_pem,
        signature=base64.b64encode(signature).decode('ascii'),
    )


def verify_attestation_token(
    token: SignedAttestationToken,
    *,
    expected_nonce: str | None = None,
    expected_public_key: str | None = None,
    allowed_issuers: tuple[str, ...] | frozenset[str] = ALLOWED_ISSUERS,
    now: datetime | None = None,
) -> AttestationClaims:
    """Verify an NRAS/nvtrust Entity Attestation Token fail-closed."""
    if content_hash(token.claims) != token.attestation_hash:
        raise OperationDeniedError('attestation token hash differs from canonical claims')
    if expected_public_key is not None and token.public_key.strip() != expected_public_key.strip():
        raise OperationDeniedError('untrusted attestation authority public key')
    try:
        raw_signature = base64.b64decode(token.signature, validate=True)
        public_key = serialization.load_pem_public_key(token.public_key.encode('ascii'))
        if not isinstance(public_key, Ed25519PublicKey):
            raise OperationDeniedError('attestation verification requires Ed25519')
        public_key.verify(raw_signature, canonical_json(token.claims).encode('utf-8'))
    except OperationDeniedError:
        raise
    except (InvalidSignature, ValueError, TypeError) as error:
        raise OperationDeniedError('attestation signature verification failed') from error

    claims = token.claims
    if claims.iss not in allowed_issuers:
        raise OperationDeniedError('untrusted attestation token issuer')
    if expected_nonce is not None and claims.eat_nonce != expected_nonce:
        raise OperationDeniedError('attestation eat_nonce differs from challenge nonce')
    if not claims.x_nvidia_gpu_arch_check:
        raise OperationDeniedError('NVIDIA GPU architecture check failed')
    if claims.x_nvidia_gpu_cc_mode != 'on':
        raise OperationDeniedError('NVIDIA GPU Confidential Computing mode must be on (devtools/off denied)')
    if not claims.x_nvidia_gpu_measurements_matching:
        raise OperationDeniedError('NVIDIA GPU RIM measurements do not match golden reference')
    if not claims.host_secure_boot or not claims.host_debug_disabled:
        raise OperationDeniedError('Confidential VM requires secure boot and debug disabled')
    try:
        issued = datetime.fromisoformat(claims.issued_at.replace('Z', '+00:00'))
        expires = datetime.fromisoformat(claims.expires_at.replace('Z', '+00:00'))
    except ValueError as error:
        raise OperationDeniedError('attestation timestamps must be ISO-8601') from error
    if issued.tzinfo is None or expires.tzinfo is None or issued >= expires or (expires - issued).total_seconds() > 300:
        raise OperationDeniedError('attestation token lifetime must be positive and bounded to 300 seconds')
    current = now or datetime.now(UTC)
    if current >= expires:
        raise OperationDeniedError('attestation token has expired')
    return claims


def evaluate_kms_key_release(
    *,
    policy: KmsKeyReleasePolicy,
    attestation: SignedAttestationToken,
    delegation: SignedDelegation,
    expected_nonce: str,
    attestation_public_key: str | None = None,
    delegation_public_key: str | None = None,
    now: datetime | None = None,
) -> KeyReleaseReceipt:
    """Evaluate a KMS Secure Key Release (SKR) request against GPU/CVM attestation and a signed delegation."""
    current = now or datetime.now(UTC)
    claims = verify_attestation_token(
        attestation,
        expected_nonce=expected_nonce,
        expected_public_key=attestation_public_key,
        allowed_issuers=policy.allowed_issuers,
        now=current,
    )
    if claims.x_nvidia_gpu_arch not in policy.allowed_gpu_archs:
        raise OperationDeniedError('GPU architecture is not permitted by KMS key-release policy')
    if claims.host_tee_type not in policy.allowed_tee_types:
        raise OperationDeniedError('Host TEE type is not permitted by KMS key-release policy')
    if claims.vtpm_pcr_sha256 not in policy.allowed_vtpm_pcrs:
        raise OperationDeniedError('vTPM PCR measurement is not in KMS allowlist')

    delegated = verify_delegation(delegation, expected_public_key=delegation_public_key, now=current)
    if delegated.workspace_id != claims.workspace_id:
        raise OperationDeniedError('delegation workspace differs from attested Confidential VM')
    if delegated.attestation_hash is not None and delegated.attestation_hash != attestation.attestation_hash:
        raise OperationDeniedError('delegation attestation binding differs from presented token')

    wrapped_material = {
        'key_id': policy.key_id,
        'run_id': delegated.run_id,
        'sandbox_id': delegated.sandbox_id,
        'attestation_hash': attestation.attestation_hash,
        'delegation_hash': delegation.delegation_hash,
    }
    return KeyReleaseReceipt(
        key_id=policy.key_id,
        workspace_id=claims.workspace_id,
        run_id=delegated.run_id,
        sandbox_id=delegated.sandbox_id,
        gpu_arch=claims.x_nvidia_gpu_arch,
        host_tee_type=claims.host_tee_type,
        attestation_hash=attestation.attestation_hash,
        delegation_hash=delegation.delegation_hash,
        wrapped_dek_sha256=content_hash(wrapped_material),
    )
