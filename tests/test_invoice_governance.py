"""Tests for SAW Layer 3 signed OpenShell policy verification and per-engagement delegation governance."""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from task_agent.console.invoice_remote_runtime import AzureInvoiceRuntime, prepare_script
from task_agent.control.invoice_agent import validate_manifest
from task_agent.control.invoice_contract import InvoicePlan
from task_agent.control.invoice_controller import InvoiceController
from task_agent.control.invoice_governance import (
    POLICY_PUBLIC_KEY_PEM,
    POLICY_SIGNATURE_BUNDLE,
    DelegationAuthority,
    prove_openshell_policy,
    sign_policy_bundle,
    verify_delegation,
    verify_policy_directory,
    verify_policy_signature,
)
from task_agent.control.invoice_repository import capability_context, new_capability
from task_agent.control.operations import OperationDeniedError
from test_invoice_agent import manifest
from test_invoice_contract import plan_values


ROOT = Path(__file__).parents[1]
OPENSHELL_DIR = ROOT / 'infra/next-phase/openshell'
IMAGE = 'crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/invoice-agent@sha256:' + 'a' * 64


@pytest.mark.parametrize(
    'policy_file',
    (
        'invoice-planning-policy.yaml',
        'invoice-execution-policy.yaml',
        'planning-policy.yaml',
        'execution-policy.yaml',
        'probe-policy.yaml',
    ),
)
def test_checked_in_openshell_policies_have_valid_ed25519_signatures(policy_file: str):
    bundle = json.loads((OPENSHELL_DIR / POLICY_SIGNATURE_BUNDLE).read_text(encoding='utf-8'))
    policy_text = (OPENSHELL_DIR / policy_file).read_text(encoding='utf-8')
    verified = verify_policy_signature(policy_file, policy_text, bundle, expected_public_key=POLICY_PUBLIC_KEY_PEM)
    assert verified['policy_file'] == policy_file
    assert verified['public_key'].strip() == POLICY_PUBLIC_KEY_PEM.strip()
    assert verify_policy_directory(OPENSHELL_DIR, policy_file, policy_text, require_signed=True) == verified


def test_policy_signature_verification_fails_closed_on_tampering():
    bundle = json.loads((OPENSHELL_DIR / POLICY_SIGNATURE_BUNDLE).read_text(encoding='utf-8'))
    policy_file = 'invoice-planning-policy.yaml'
    policy_text = (OPENSHELL_DIR / policy_file).read_text(encoding='utf-8')

    with pytest.raises(OperationDeniedError, match='content differs'):
        verify_policy_signature(policy_file, policy_text + '\n# tampered\n', bundle)

    tampered_sig = deepcopy(bundle)
    raw = bytearray(base64.b64decode(tampered_sig['policies'][policy_file]['signature']))
    raw[0] ^= 0xFF
    tampered_sig['policies'][policy_file]['signature'] = base64.b64encode(bytes(raw)).decode('ascii')
    with pytest.raises(OperationDeniedError, match='signature verification failed'):
        verify_policy_signature(policy_file, policy_text, tampered_sig)

    rogue_key = Ed25519PrivateKey.generate()
    rogue_bundle = sign_policy_bundle({policy_file: policy_text}, rogue_key)
    with pytest.raises(OperationDeniedError, match='untrusted OpenShell policy signing key'):
        verify_policy_signature(policy_file, policy_text, rogue_bundle, expected_public_key=POLICY_PUBLIC_KEY_PEM)


def test_remote_prepare_embeds_and_executes_host_ed25519_policy_verification(tmp_path: Path):
    for name in ('invoice-planning-policy.yaml', POLICY_SIGNATURE_BUNDLE):
        (tmp_path / name).write_text((OPENSHELL_DIR / name).read_text(encoding='utf-8'), encoding='utf-8')
    policy_text = (tmp_path / 'invoice-planning-policy.yaml').read_text(encoding='utf-8')
    verified = verify_policy_directory(tmp_path, 'invoice-planning-policy.yaml', policy_text, require_signed=True)

    script = prepare_script('planning', 'b' * 32, IMAGE, policy_text, 'operator-managed', policy_signature=verified)
    source = script.split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    compile(source, '<signed-policy-prepare>', 'exec')
    assert "sig_meta['sha256']" in source and ".verify(" in source

    policy_path = tmp_path / 'staged.yaml'
    policy_path.write_bytes(policy_text.replace('\r\n', '\n').encode('utf-8'))
    block = source.split("os.chmod(policy,0o644)\n", 1)[1].split("disk=os.statvfs(root)", 1)[0]
    scope = {'json': json, 'base64': base64, 'hashlib': hashlib, 'serialization': serialization, 'policy': policy_path}
    exec(block, scope)

    policy_path.write_bytes((policy_text + '\n# tampered on host\n').encode('utf-8'))
    with pytest.raises(AssertionError, match='host policy bytes differ'):
        exec(block, scope)


def test_runtime_rejects_unsigned_or_tampered_policy_before_host_command(tmp_path: Path):
    policy_text = (OPENSHELL_DIR / 'invoice-planning-policy.yaml').read_text(encoding='utf-8')
    (tmp_path / 'invoice-planning-policy.yaml').write_text(policy_text, encoding='utf-8')
    runtime = AzureInvoiceRuntime(compute_client=None, image=IMAGE, policies=tmp_path, require_signed_policy=True)
    runtime.command = lambda _script: pytest.fail('must not invoke host command when policy bundle is missing')
    with pytest.raises(OperationDeniedError, match='signed OpenShell policy bundle required'):
        runtime.prepare('planning', 'b' * 32)

    (tmp_path / POLICY_SIGNATURE_BUNDLE).write_text((OPENSHELL_DIR / POLICY_SIGNATURE_BUNDLE).read_text(encoding='utf-8'), encoding='utf-8')
    (tmp_path / 'invoice-planning-policy.yaml').write_text(policy_text + '\n# drift\n', encoding='utf-8')
    with pytest.raises(OperationDeniedError, match='content differs'):
        runtime.prepare('planning', 'b' * 32)


def test_delegation_authority_binds_all_saw_fields_and_rejects_tampering():
    authority = DelegationAuthority()
    now = datetime.now(UTC)
    expiry = now + timedelta(minutes=8)
    plan = InvoicePlan(**plan_values())

    planning_del = authority.issue(
        kind='planning',
        run_id='a' * 32,
        sponsor_hash='1' * 64,
        sandbox_id='b' * 32,
        target_id='c' * 32,
        policy_hash='2' * 64,
        capability_hash='3' * 64,
        issued_at=now,
        expires_at=expiry,
    )
    verified = verify_delegation(planning_del, expected_public_key=authority.public_key_pem, now=now)
    assert verified.logical_agent_id == 'agentrunner-invoice-planning'
    assert verified.approval_mode == 'propose-only'
    assert verified.permitted_tools == ('invoice_summary', 'invoice_batches', 'publish_decision')
    assert verified.revocation_ref == 'control.usp_revoke_invoice_run:' + ('a' * 32)

    exec_del = authority.issue(
        kind='execution',
        run_id='d' * 32,
        sponsor_hash=plan.sponsor_hash,
        sandbox_id='e' * 32,
        target_id=plan.plan_id,
        policy_hash='4' * 64,
        capability_hash='5' * 64,
        issued_at=now,
        expires_at=expiry,
        plan_hash=plan.plan_hash,
    )
    assert verify_delegation(exec_del, expected_public_key=authority.public_key_pem, now=now).plan_hash == plan.plan_hash

    with pytest.raises(OperationDeniedError, match='expired'):
        verify_delegation(planning_del, now=expiry + timedelta(seconds=1))

    other_authority = DelegationAuthority()
    with pytest.raises(OperationDeniedError, match='untrusted delegation signing key'):
        verify_delegation(planning_del, expected_public_key=other_authority.public_key_pem)


def test_manifest_validation_enforces_signed_delegation_bindings():
    authority = DelegationAuthority()
    base = manifest('execution')
    now = datetime.now(UTC)
    expiry = datetime.fromisoformat(base.expires_at.replace('Z', '+00:00'))
    cap_hash = capability_context(base.capability.get_secret_value())[1]
    delegation = authority.issue(
        kind='execution',
        run_id=base.run_id,
        sponsor_hash=base.plan.sponsor_hash,
        sandbox_id=base.sandbox_id,
        target_id=base.plan.plan_id,
        policy_hash='9' * 64,
        capability_hash=cap_hash,
        issued_at=now,
        expires_at=expiry,
        plan_hash=base.plan.plan_hash,
    )
    bound = base.model_copy(update={'delegation': delegation})
    validate_manifest(bound, now=now)

    from pydantic import SecretStr
    wrong_cap = base.model_copy(update={'capability': SecretStr(new_capability(base.run_id)), 'delegation': delegation})
    with pytest.raises(ValueError, match='capability hash differs'):
        validate_manifest(wrong_cap, now=now)

    wrong_sandbox = bound.model_copy(update={'sandbox_id': 'f' * 32})
    with pytest.raises(ValueError, match='delegation binding differs'):
        validate_manifest(wrong_sandbox, now=now)


@pytest.mark.asyncio
async def test_controller_embeds_signed_delegation_in_manifest_and_audit_events():
    plan = InvoicePlan(**plan_values())
    events, manifests = [], []

    class Runtime:
        def prepare(self, kind, run_id):
            return {'run_id': run_id, 'sandbox_id': 'b' * 32, 'policy_hash': 'a' * 64}

        async def execute(self, run_manifest, on_event):
            manifests.append(run_manifest)
            validate_manifest(run_manifest)
            await on_event({'source': 'agent-runtime', 'event_type': 'agent-finished', 'independently_verified': False})
            return {}

        def stop(self, kind, run_id):
            pass

    async def call(name, values):
        return [{'plan_json': plan.model_dump_json()}] if 'claim' in name else [{'run_id': values['run_id']}]

    async def plans(_sponsor):
        return [{
            'plan_json': plan.model_dump(mode='json'),
            'plan_hash': plan.plan_hash,
            'state': 'approved',
            'execution_before': (datetime.now(UTC) + timedelta(minutes=8)).isoformat(),
            'approval_expires_at': datetime.now(UTC) + timedelta(minutes=10),
        }]

    async def verify(_run_id):
        return {'fixture_checks': True}

    async def audit(**event):
        events.append(event['event'])

    authority = DelegationAuthority()
    controller = InvoiceController(
        admission_client=SimpleNamespace(call=call),
        incident_repository=SimpleNamespace(plans=plans),
        verifier_repository=SimpleNamespace(verify=verify),
        runtime=Runtime(),
        audit=audit,
        delegation_authority=authority,
    )
    await controller.execute(plan.plan_id, plan.plan_hash, sponsor_hash=plan.sponsor_hash)
    assert len(manifests) == 1 and manifests[0].delegation is not None
    delegation = manifests[0].delegation
    assert verify_delegation(delegation, expected_public_key=authority.public_key_pem).plan_hash == plan.plan_hash
    issued_event = next(item for item in events if item['event_type'] == 'authority-issued')
    revoked_event = next(item for item in events if item['event_type'] == 'authority-revoked')
    assert issued_event['delegation_hash'] == delegation.delegation_hash
    assert revoked_event['delegation_hash'] == delegation.delegation_hash


def test_openshell_policy_prover_verifies_checked_in_policies_and_flags_risky_diffs():
    import yaml

    planning_text = (OPENSHELL_DIR / 'invoice-planning-policy.yaml').read_text(encoding='utf-8')
    execution_text = (OPENSHELL_DIR / 'invoice-execution-policy.yaml').read_text(encoding='utf-8')
    assert prove_openshell_policy(planning_text) == []
    assert prove_openshell_policy(execution_text) == []

    # Flag IMDS / SQL / new host / new route / new binary / writable /app
    candidate = yaml.safe_load(planning_text)
    candidate['filesystem_policy']['read_write'] = ['/tmp', '/app']
    candidate['network_policies']['invoice_planning']['binaries'].append({'path': '/usr/bin/curl'})
    endpoint = candidate['network_policies']['invoice_planning']['endpoints'][0]
    endpoint['rules'].append({'allow': {'method': 'DELETE', 'path': '/v2/invoice/tools/invoice_summary'}})
    candidate['network_policies']['imds'] = {
        'name': 'imds',
        'endpoints': [{
            'host': '169.254.169.254',
            'port': 80,
            'protocol': 'rest',
            'enforcement': 'enforce',
            'rules': [{'allow': {'method': 'GET', 'path': '/metadata/instance'}}],
        }],
        'binaries': [{'path': '/usr/local/bin/python3.12'}],
    }
    findings = prove_openshell_policy(candidate, baseline_text=planning_text)
    assert 'writable_paths_exceed_tmp' in findings
    assert 'forbidden_or_wildcard_host:169.254.169.254' in findings
    assert 'non_tls_or_sql_port:80' in findings
    assert 'new_credentialed_host:169.254.169.254' in findings
    assert 'new_executable_binary:/usr/bin/curl' in findings
    assert any(item.startswith('new_http_method_or_route:') and 'DELETE' in item for item in findings)
