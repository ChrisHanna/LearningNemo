"""Signed OpenShell policy verification and per-engagement delegation governance (SAW Layer 3)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field
import yaml

from task_agent.control.canonical import canonical_json, content_hash
from task_agent.control.invoice_contract import Digest, Identifier
from task_agent.control.operations import OperationDeniedError


POLICY_SIGNATURE_BUNDLE = 'policy-signatures.json'
POLICY_KEY_ID = 'learningnemo-openshell-policy-ed25519-v1'
POLICY_PUBLIC_KEY_PEM = (
    '-----BEGIN PUBLIC KEY-----\n'
    'MCowBQYDK2VwAyEAaBtLfW2RDrp2hUBECF+D2J7l/XiDQuNKA0bHSIdNstk=\n'
    '-----END PUBLIC KEY-----\n'
)
DELEGATION_KEY_ID = 'learningnemo-control-delegation-ed25519-v1'
WORKSPACE_ID = 'vm-learningnemo-saw-dev'

ROLE_TOOLS: dict[str, tuple[str, ...]] = {
    'planning': ('invoice_summary', 'invoice_batches', 'publish_decision'),
    'execution': ('execute_step',),
}

FORBIDDEN_HOSTS = frozenset({
    '169.254.169.254',
    '168.63.129.16',
    'metadata.google.internal',
    'localhost',
    '127.0.0.1',
    '0.0.0.0',
})


def prove_openshell_policy(
    policy_text: str | dict,
    *,
    baseline_text: str | dict | None = None,
    require_hard_landlock: bool = True,
) -> list[str]:
    """Static OpenShell policy prover (openshell-prover semantics) checking boundary and diff invariants."""
    findings: list[str] = []
    parsed = yaml.safe_load(policy_text) if isinstance(policy_text, str) else policy_text
    if not isinstance(parsed, dict) or parsed.get('version') != 1:
        return ['invalid_policy_version']

    process = parsed.get('process') or {}
    if process.get('run_as_user') != 'sandbox' or process.get('run_as_group') != 'sandbox':
        findings.append('non_sandbox_process_identity')

    landlock = parsed.get('landlock') or {}
    compatibility = landlock.get('compatibility')
    if require_hard_landlock and compatibility != 'hard_requirement':
        findings.append('landlock_not_hard_requirement')
    elif compatibility not in ('hard_requirement', 'best_effort'):
        findings.append('invalid_landlock_compatibility')

    filesystem = parsed.get('filesystem_policy') or {}
    if filesystem.get('include_workdir') is not False:
        findings.append('workdir_implicitly_exposed')
    read_write = filesystem.get('read_write') or []
    if read_write != ['/tmp']:
        findings.append('writable_paths_exceed_tmp')

    network_policies = parsed.get('network_policies')
    if not isinstance(network_policies, dict):
        findings.append('missing_network_policies')
        return findings

    def extract_reach(doc: dict) -> tuple[set[str], set[tuple[str, int, str, str]], set[str]]:
        hosts: set[str] = set()
        routes: set[tuple[str, int, str, str]] = set()
        binaries: set[str] = set()
        for net in (doc.get('network_policies') or {}).values():
            if not isinstance(net, dict):
                continue
            for binary in net.get('binaries') or ():
                if isinstance(binary, dict) and isinstance(binary.get('path'), str):
                    binaries.add(binary['path'])
            for endpoint in net.get('endpoints') or ():
                if not isinstance(endpoint, dict):
                    continue
                host = str(endpoint.get('host') or '').strip().lower()
                port = endpoint.get('port')
                hosts.add(host)
                for rule in endpoint.get('rules') or ():
                    allow = rule.get('allow') if isinstance(rule, dict) else None
                    if isinstance(allow, dict):
                        routes.add((host, int(port or 0), str(allow.get('method') or ''), str(allow.get('path') or '')))
        return hosts, routes, binaries

    for name, net in network_policies.items():
        if not isinstance(net, dict):
            findings.append(f'malformed_network_policy:{name}')
            continue
        for endpoint in net.get('endpoints') or ():
            if not isinstance(endpoint, dict):
                findings.append(f'malformed_endpoint:{name}')
                continue
            host = str(endpoint.get('host') or '').strip().lower()
            port = endpoint.get('port')
            if not host or '*' in host or host in FORBIDDEN_HOSTS or host.endswith('.database.windows.net'):
                findings.append(f'forbidden_or_wildcard_host:{host or "empty"}')
            if port != 443:
                findings.append(f'non_tls_or_sql_port:{port}')
            if 'tls' in endpoint:
                findings.append('deprecated_tls_terminate_field')
            if endpoint.get('protocol') != 'rest' or endpoint.get('enforcement') != 'enforce':
                findings.append(f'uninspected_or_unenforced_endpoint:{host}')
            rules = endpoint.get('rules') or []
            if not rules or 'access' in endpoint:
                findings.append(f'broad_or_missing_l7_rules:{host}')
            for rule in rules:
                allow = rule.get('allow') if isinstance(rule, dict) else None
                path = str((allow or {}).get('path') or '')
                method = str((allow or {}).get('method') or '')
                if not allow or not method or not path.startswith('/') or '*' in path:
                    findings.append(f'wildcard_or_invalid_l7_rule:{host}:{method}:{path}')

    if baseline_text is not None:
        baseline = yaml.safe_load(baseline_text) if isinstance(baseline_text, str) else baseline_text
        if isinstance(baseline, dict):
            base_hosts, base_routes, base_bins = extract_reach(baseline)
            cand_hosts, cand_routes, cand_bins = extract_reach(parsed)
            for new_host in sorted(cand_hosts - base_hosts):
                findings.append(f'new_credentialed_host:{new_host}')
            for host, port, method, path in sorted(cand_routes - base_routes):
                findings.append(f'new_http_method_or_route:{host}:{port}:{method}:{path}')
            for new_bin in sorted(cand_bins - base_bins):
                findings.append(f'new_executable_binary:{new_bin}')

    return findings


def policy_statement(policy_file: str, policy_text: str, *, key_id: str = POLICY_KEY_ID) -> dict[str, str | int]:
    """Canonical statement bound by an OpenShell policy signature."""
    if not policy_file or '/' in policy_file or '\\' in policy_file:
        raise ValueError('exact OpenShell policy filename required')
    normalized = policy_text.replace('\r\n', '\n')
    parsed = yaml.safe_load(normalized)
    if not isinstance(parsed, dict) or 'version' not in parsed:
        raise ValueError('OpenShell policy must be a versioned YAML mapping')
    return {
        'version': 1,
        'key_id': key_id,
        'policy_file': policy_file,
        'policy_hash': content_hash(parsed),
        'sha256': hashlib.sha256(normalized.encode('utf-8')).hexdigest(),
    }


def sign_policy_bundle(
    policies: dict[str, str],
    private_key: Ed25519PrivateKey,
    *,
    key_id: str = POLICY_KEY_ID,
) -> dict:
    """Sign a mapping of policy filenames to YAML text with an Ed25519 private key."""
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode('ascii')
    entries = {}
    for name in sorted(policies):
        statement = policy_statement(name, policies[name], key_id=key_id)
        signature = private_key.sign(canonical_json(statement).encode('utf-8'))
        entries[name] = {
            'policy_hash': statement['policy_hash'],
            'sha256': statement['sha256'],
            'signature': base64.b64encode(signature).decode('ascii'),
        }
    return {
        'version': 1,
        'key_id': key_id,
        'public_key': public_pem,
        'policies': entries,
    }


def verify_policy_signature(
    policy_file: str,
    policy_text: str,
    bundle: dict,
    *,
    expected_public_key: str | None = POLICY_PUBLIC_KEY_PEM,
) -> dict[str, str]:
    """Verify that an OpenShell policy matches its signed Ed25519 governance bundle."""
    if not isinstance(bundle, dict) or bundle.get('version') != 1:
        raise OperationDeniedError('invalid OpenShell policy signature bundle')
    key_id = bundle.get('key_id')
    public_pem = bundle.get('public_key')
    if not isinstance(key_id, str) or not key_id or not isinstance(public_pem, str) or not public_pem:
        raise OperationDeniedError('missing OpenShell policy signing key metadata')
    if expected_public_key is not None and public_pem.strip() != expected_public_key.strip():
        raise OperationDeniedError('untrusted OpenShell policy signing key')
    policies = bundle.get('policies')
    if not isinstance(policies, dict) or policy_file not in policies:
        raise OperationDeniedError(f'missing OpenShell policy signature for {policy_file}')
    entry = policies[policy_file]
    if not isinstance(entry, dict):
        raise OperationDeniedError('malformed OpenShell policy signature entry')
    try:
        statement = policy_statement(policy_file, policy_text, key_id=key_id)
    except Exception as error:
        raise OperationDeniedError('invalid OpenShell policy document') from error
    if entry.get('policy_hash') != statement['policy_hash'] or entry.get('sha256') != statement['sha256']:
        raise OperationDeniedError('OpenShell policy content differs from signed digest')
    try:
        raw_signature = base64.b64decode(entry.get('signature', ''), validate=True)
        public_key = serialization.load_pem_public_key(public_pem.encode('ascii'))
        if not isinstance(public_key, Ed25519PublicKey):
            raise OperationDeniedError('OpenShell policy verification requires Ed25519')
        public_key.verify(raw_signature, canonical_json(statement).encode('utf-8'))
    except OperationDeniedError:
        raise
    except (InvalidSignature, ValueError, TypeError) as error:
        raise OperationDeniedError('OpenShell policy signature verification failed') from error
    return {
        'policy_file': policy_file,
        'policy_hash': str(statement['policy_hash']),
        'sha256': str(statement['sha256']),
        'key_id': key_id,
        'public_key': public_pem,
        'signature': str(entry['signature']),
    }


def verify_policy_directory(
    directory: Path,
    policy_file: str,
    policy_text: str,
    *,
    require_signed: bool = False,
    expected_public_key: str | None = POLICY_PUBLIC_KEY_PEM,
) -> dict[str, str] | None:
    """Verify policy_file against policy-signatures.json in directory when present or required."""
    bundle_path = Path(directory) / POLICY_SIGNATURE_BUNDLE
    if not bundle_path.is_file():
        if require_signed:
            raise OperationDeniedError('signed OpenShell policy bundle required')
        return None
    import json
    try:
        bundle = json.loads(bundle_path.read_text(encoding='utf-8'))
    except Exception as error:
        raise OperationDeniedError('unreadable OpenShell policy signature bundle') from error
    verified = verify_policy_signature(policy_file, policy_text, bundle, expected_public_key=expected_public_key)
    if policy_file.startswith('invoice-'):
        findings = prove_openshell_policy(policy_text, require_hard_landlock=True)
        if findings:
            raise OperationDeniedError('OpenShell policy prover rejected policy: ' + ', '.join(findings))
    return verified


class DelegationRecord(BaseModel):
    """Per-engagement delegation record binding sponsor, workspace, agent, sandbox, policy, and capability."""

    model_config = ConfigDict(extra='forbid', frozen=True)
    version: Literal[1] = 1
    run_id: Identifier
    sponsor_hash: Digest
    workspace_id: Literal['vm-learningnemo-saw-dev'] = WORKSPACE_ID
    logical_agent_id: Literal['agentrunner-invoice-planning', 'agentrunner-invoice-execution']
    sandbox_id: Identifier
    kind: Literal['planning', 'execution']
    target_id: Identifier
    plan_hash: Digest | None = None
    policy_hash: Digest
    capability_hash: Digest
    attestation_hash: Digest | None = None
    permitted_tools: tuple[str, ...]
    scoped_resources: tuple[str, ...]
    approval_mode: Literal['propose-only', 'human-approved-plan']
    issued_at: str
    expires_at: str
    revocation_ref: str = Field(min_length=1, max_length=120)
    audit_ref: str = Field(min_length=1, max_length=120)


class SignedDelegation(BaseModel):
    """Ed25519-signed delegation record verified before sandbox agent execution."""

    model_config = ConfigDict(extra='forbid', frozen=True)
    record: DelegationRecord
    delegation_hash: Digest
    key_id: str = Field(min_length=1, max_length=80)
    public_key: str = Field(min_length=32, max_length=256)
    signature: str = Field(min_length=64, max_length=256)


def validate_delegation_semantics(record: DelegationRecord, *, now: datetime | None = None) -> None:
    """Check role, tool, approval-mode, revocation, and lifetime invariants on a DelegationRecord."""
    expected_agent = 'agentrunner-invoice-' + record.kind
    expected_mode = 'propose-only' if record.kind == 'planning' else 'human-approved-plan'
    expected_tools = ROLE_TOOLS[record.kind]
    if record.logical_agent_id != expected_agent or record.approval_mode != expected_mode:
        raise OperationDeniedError('delegation logical agent or approval mode differs from role')
    if record.permitted_tools != expected_tools:
        raise OperationDeniedError('delegation permitted tools differ from role allowlist')
    if (record.kind == 'execution') != (record.plan_hash is not None):
        raise OperationDeniedError('execution delegation requires an exact approved plan hash')
    expected_resources = (
        (f'invoice-scenario:{record.target_id}', 'gateway:invoice-planning')
        if record.kind == 'planning'
        else (f'invoice-plan:{record.target_id}:{record.plan_hash}', 'gateway:invoice-execution')
    )
    if record.scoped_resources != expected_resources:
        raise OperationDeniedError('delegation scoped resources differ from bound target')
    if record.revocation_ref != f'control.usp_revoke_invoice_run:{record.run_id}':
        raise OperationDeniedError('delegation revocation reference differs from run binding')
    if record.audit_ref != f'control.InvoiceActivity:{record.run_id}':
        raise OperationDeniedError('delegation audit reference differs from run binding')
    try:
        issued = datetime.fromisoformat(record.issued_at.replace('Z', '+00:00'))
        expires = datetime.fromisoformat(record.expires_at.replace('Z', '+00:00'))
    except ValueError as error:
        raise OperationDeniedError('delegation timestamps must be ISO-8601') from error
    if issued.tzinfo is None or expires.tzinfo is None or issued >= expires or (expires - issued).total_seconds() > 900:
        raise OperationDeniedError('delegation lifetime must be positive and bounded to 900 seconds')
    if now is not None and now >= expires:
        raise OperationDeniedError('delegation record has expired')


def verify_delegation(
    delegation: SignedDelegation,
    *,
    expected_public_key: str | None = None,
    now: datetime | None = None,
) -> DelegationRecord:
    """Verify a SignedDelegation's hash, role invariants, and Ed25519 signature."""
    if content_hash(delegation.record) != delegation.delegation_hash:
        raise OperationDeniedError('delegation hash differs from canonical record')
    validate_delegation_semantics(delegation.record, now=now)
    if expected_public_key is not None and delegation.public_key.strip() != expected_public_key.strip():
        raise OperationDeniedError('untrusted delegation signing key')
    try:
        raw_signature = base64.b64decode(delegation.signature, validate=True)
        public_key = serialization.load_pem_public_key(delegation.public_key.encode('ascii'))
        if not isinstance(public_key, Ed25519PublicKey):
            raise OperationDeniedError('delegation signature verification requires Ed25519')
        public_key.verify(raw_signature, canonical_json(delegation.record).encode('utf-8'))
    except OperationDeniedError:
        raise
    except (InvalidSignature, ValueError, TypeError) as error:
        raise OperationDeniedError('delegation signature verification failed') from error
    return delegation.record


class DelegationAuthority:
    """Control-plane Ed25519 signer and verifier for per-engagement AgentRunner delegations."""

    def __init__(self, private_key: Ed25519PrivateKey | None = None, *, key_id: str = DELEGATION_KEY_ID):
        if private_key is None:
            configured = os.environ.get('LEARNINGNEMO_DELEGATION_PRIVATE_KEY')
            if configured:
                loaded = serialization.load_pem_private_key(configured.encode('ascii'), password=None)
                if not isinstance(loaded, Ed25519PrivateKey):
                    raise ValueError('LEARNINGNEMO_DELEGATION_PRIVATE_KEY must be an Ed25519 private key')
                private_key = loaded
            else:
                private_key = Ed25519PrivateKey.generate()
        self._private_key = private_key
        self.key_id = key_id
        self.public_key_pem = self._private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode('ascii')

    def issue(
        self,
        *,
        kind: Literal['planning', 'execution'],
        run_id: str,
        sponsor_hash: str,
        sandbox_id: str,
        target_id: str,
        policy_hash: str,
        capability_hash: str,
        issued_at: datetime,
        expires_at: datetime,
        plan_hash: str | None = None,
        attestation_hash: str | None = None,
    ) -> SignedDelegation:
        resources = (
            (f'invoice-scenario:{target_id}', 'gateway:invoice-planning')
            if kind == 'planning'
            else (f'invoice-plan:{target_id}:{plan_hash}', 'gateway:invoice-execution')
        )
        record = DelegationRecord(
            run_id=run_id,
            sponsor_hash=sponsor_hash,
            logical_agent_id=f'agentrunner-invoice-{kind}',
            sandbox_id=sandbox_id,
            kind=kind,
            target_id=target_id,
            plan_hash=plan_hash,
            policy_hash=policy_hash,
            capability_hash=capability_hash,
            attestation_hash=attestation_hash,
            permitted_tools=ROLE_TOOLS[kind],
            scoped_resources=resources,
            approval_mode='propose-only' if kind == 'planning' else 'human-approved-plan',
            issued_at=issued_at.isoformat(),
            expires_at=expires_at.isoformat(),
            revocation_ref=f'control.usp_revoke_invoice_run:{run_id}',
            audit_ref=f'control.InvoiceActivity:{run_id}',
        )
        validate_delegation_semantics(record, now=issued_at)
        signature = self._private_key.sign(canonical_json(record).encode('utf-8'))
        signed = SignedDelegation(
            record=record,
            delegation_hash=content_hash(record),
            key_id=self.key_id,
            public_key=self.public_key_pem,
            signature=base64.b64encode(signature).decode('ascii'),
        )
        verify_delegation(signed, expected_public_key=self.public_key_pem, now=issued_at)
        return signed
