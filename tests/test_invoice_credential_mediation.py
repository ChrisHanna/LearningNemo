"""Provider-mediated run capabilities: the sandbox agent sees only an OpenShell placeholder."""

import base64
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from task_agent.console.invoice_remote_runtime import (AzureInvoiceRuntime, expire_provider_script, prepare_script,
    provider_block, provider_name)
from task_agent.control.invoice_agent import AgentSession, PLACEHOLDER_PREFIX, run_credential, validate_manifest
from task_agent.control.invoice_controller import run_authority
from task_agent.control.invoice_repository import capability_context
from task_agent.control.operations import OperationDeniedError
from test_invoice_agent import manifest


ROOT = Path(__file__).parents[1]
IMAGE = 'crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/invoice-agent@sha256:' + 'a' * 64
RUN_ID = 'b' * 32
PLACEHOLDER = PLACEHOLDER_PREFIX + 'v2_INVOICE_RUN_CAPABILITY'


def profile(kind='planning'):
    return yaml.safe_load((ROOT / f'infra/next-phase/openshell/invoice-{kind}-provider-profile.yaml').read_text())


def host_source(script):
    return script.split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]


@pytest.mark.parametrize('kind', ('planning', 'execution'))
def test_profiles_bind_the_capability_only_to_the_role_gateway_routes(kind):
    policy = yaml.safe_load((ROOT / f'infra/next-phase/openshell/invoice-{kind}-policy.yaml').read_text())
    network = next(iter(policy['network_policies'].values()))
    value = profile(kind)
    assert value['id'] == 'learningnemo-invoice-' + kind
    assert value['credentials'] == [{'name': 'run_capability', 'description': value['credentials'][0]['description'],
        'env_vars': ['INVOICE_RUN_CAPABILITY'], 'required': True, 'auth_style': 'bearer', 'header_name': 'authorization'}]
    [endpoint] = value['endpoints']
    assert endpoint['host'] == network['endpoints'][0]['host'] and endpoint['port'] == 443
    assert endpoint['path'] == '/v2/invoice/**' and endpoint['enforcement'] == 'enforce'
    assert endpoint['rules'] == network['endpoints'][0]['rules']
    assert value['binaries'] == [binary['path'] for binary in network['binaries']]


def test_agent_uses_the_placeholder_and_refuses_a_raw_capability():
    mediated = manifest().model_copy(update={'capability': None})
    validate_manifest(mediated)
    assert run_credential(mediated, {'INVOICE_RUN_CAPABILITY': PLACEHOLDER}) == PLACEHOLDER
    for value in ('', 'a' * 32 + '.' + 'x' * 43, PLACEHOLDER_PREFIX + 'OPENAI_API_KEY'):
        with pytest.raises(ValueError, match='placeholder required'):
            run_credential(mediated, {'INVOICE_RUN_CAPABILITY': value})


@pytest.mark.asyncio
async def test_agent_tool_calls_send_only_the_placeholder():
    mediated = manifest().model_copy(update={'capability': None})
    seen = []
    def handle(request):
        seen.append(request.headers['authorization'])
        return httpx.Response(200, json={'fixture': True})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        session = AgentSession(mediated, client, lambda *args, **kwargs: None, PLACEHOLDER)
        await session.call('invoice_summary')
    assert seen == ['Bearer ' + PLACEHOLDER]


def test_prepare_mints_into_a_provider_before_creating_the_sandbox():
    policy = (ROOT / 'infra/next-phase/openshell/invoice-planning-policy.yaml').read_text()
    source = host_source(prepare_script('planning', RUN_ID, IMAGE, policy, 'operator-managed', profile()))
    compile(source, '<provider-prepare>', 'exec')
    assert source.index("cli('provider','profile','export'") < source.index("secret_cli('provider','create'") \
        < source.index("cli('sandbox','create'")
    assert "*provider_arguments" in source and "'--provider',provider" in source
    assert "'--credential','INVOICE_RUN_CAPABILITY',secret=capability" in source and 'del capability' in source
    assert "**mediation" in source and "'INVOICE_RUN_CAPABILITY'+'=1'" in source
    manifest_mode = host_source(prepare_script('planning', RUN_ID, IMAGE, policy, 'operator-managed'))
    assert 'provider' not in manifest_mode.split('try:\n', 1)[1].split("cli('sandbox','create'", 1)[0]
    with pytest.raises(ValueError, match='role'):
        prepare_script('planning', RUN_ID, IMAGE, policy, 'operator-managed', profile('execution'))


def run_provider_block(tmp_path, exported):
    calls, secrets = [], []
    def cli(*args, timeout=300):
        calls.append(args)
        if args[:3] == ('provider', 'profile', 'export'):
            if exported[0] is None:
                raise RuntimeError('absent')
            return json.dumps(exported[0])
        if args[:3] == ('provider', 'profile', 'import'):
            exported[0] = json.loads(Path(args[4]).read_text())
        return ''
    def run(argv, **kwargs):
        secrets.append((argv, kwargs['env']['INVOICE_RUN_CAPABILITY']))
        return SimpleNamespace(returncode=0)
    scope = dict(json=json, base64=base64, os=os, subprocess=SimpleNamespace(run=run), cli=cli,
                 policies=tmp_path, environment={'HOME': '/home/sawadmin'})
    exec(provider_block('planning', RUN_ID, profile()), scope)
    return scope, calls, secrets


def test_provider_block_imports_once_and_keeps_the_capability_out_of_arguments(tmp_path):
    exported = [None]
    scope, calls, secrets = run_provider_block(tmp_path, exported)
    assert [call[:3] for call in calls] == [('provider', 'profile', 'export'), ('provider', 'profile', 'import'),
                                            ('provider', 'profile', 'export')]
    [(argv, capability)] = secrets
    assert capability_context(capability)[0] == RUN_ID
    assert capability not in ' '.join(argv)
    assert argv[-8:] == ['provider', 'create', '--name', provider_name(RUN_ID), '--type', 'learningnemo-invoice-planning',
                         '--credential', 'INVOICE_RUN_CAPABILITY']
    assert scope['mediation'] == {'capability_hash': hashlib.sha256(capability.encode('ascii')).hexdigest(),
                                  'provider': provider_name(RUN_ID)}
    assert scope['provider_arguments'] == ['--provider', provider_name(RUN_ID)] and 'capability' not in scope
    _, calls, _ = run_provider_block(tmp_path, exported)
    assert ('provider', 'profile', 'import') not in [call[:3] for call in calls]


def test_provider_block_refuses_a_drifted_profile(tmp_path):
    drifted = profile()
    drifted['endpoints'][0]['host'] = 'uploads.example.com'
    with pytest.raises(AssertionError, match='binding drift'):
        run_provider_block(tmp_path, [drifted])


def test_stop_expires_the_credential_and_tolerates_a_provider_never_minted():
    source = expire_provider_script(RUN_ID)
    updates = []
    def cli(*args):
        updates.append(args)
        if args[1] == 'update' and state['update_fails']:
            raise RuntimeError('failed')
        if args[1] == 'get' and not state['exists']:
            raise RuntimeError('absent')
    for update_fails, exists, raises in ((False, True, False), (True, False, False), (True, True, True)):
        state = {'update_fails': update_fails, 'exists': exists}
        if raises:
            with pytest.raises(RuntimeError):
                exec(source, {'cli': cli})
        else:
            exec(source, {'cli': cli})
    assert updates[0] == ('provider', 'update', provider_name(RUN_ID), '--credential-expires-at', 'INVOICE_RUN_CAPABILITY=1')


def test_runtime_requires_a_provider_hash_only_in_provider_mode(tmp_path):
    for name in ('invoice-planning-policy.yaml', 'invoice-planning-provider-profile.yaml'):
        (tmp_path / name).write_text((ROOT / 'infra/next-phase/openshell' / name).read_text())
    policy = (tmp_path / 'invoice-planning-policy.yaml').read_text()
    base = {'run_id': RUN_ID, 'sandbox_id': 'c' * 32, 'name': 'ip-' + RUN_ID[:16], 'policy': policy, 'public_key': '', 'image': IMAGE}
    mediated = {**base, 'capability_hash': 'd' * 64, 'provider': provider_name(RUN_ID)}
    for mode, record, ok in (('provider', mediated, True), ('provider', base, False), ('manifest', mediated, False),
                             ('manifest', base, True), ('provider', {**mediated, 'capability_hash': 'x'}, False)):
        runtime = AzureInvoiceRuntime(compute_client=None, image=IMAGE, policies=tmp_path, credential_mode=mode)
        scripts, stopped = [], []
        runtime.command = lambda script, timeout=600, record=record: scripts.append(script) or 'INVOICE_PREPARED ' + json.dumps(record)
        runtime.stop = lambda kind, run_id: stopped.append(run_id)
        if ok:
            prepared = runtime.prepare('planning', RUN_ID)
            assert prepared.get('capability_hash') == record.get('capability_hash') and not stopped
            assert ("secret_cli('provider','create'" in scripts[0]) == (mode == 'provider')
        else:
            with pytest.raises(RuntimeError, match='capability'):
                runtime.prepare('planning', RUN_ID)
            assert stopped == [RUN_ID]
    with pytest.raises(ValueError):
        AzureInvoiceRuntime(compute_client=None, image=IMAGE, policies=tmp_path, credential_mode='environment')


@pytest.mark.asyncio
async def test_runtime_refuses_a_manifest_that_differs_from_its_credential_mode(tmp_path):
    runtime = AzureInvoiceRuntime(compute_client=None, image=IMAGE, policies=tmp_path, credential_mode='provider')
    context = manifest()
    runtime.prepared[context.run_id] = {'sandbox_id': context.sandbox_id, 'public_key': ''}
    with pytest.raises(ValueError, match='credential mode'):
        await runtime.execute(context, None)


def test_controller_registers_the_provider_hash_without_holding_the_capability():
    assert run_authority(RUN_ID, {'capability_hash': 'e' * 64}) == (None, 'e' * 64)
    with pytest.raises(OperationDeniedError):
        run_authority(RUN_ID, {'capability_hash': 'not-a-hash'})
    capability, digest = run_authority(RUN_ID, {})
    assert capability_context(capability) == (RUN_ID, digest)


def test_services_image_and_deployment_carry_the_profiles_and_mode():
    dockerfile = (ROOT / 'containers/invoice-services.Dockerfile').read_text()
    build = (ROOT / 'scripts/build-invoice-services.sh').read_text()
    for kind in ('planning', 'execution'):
        path = f'infra/next-phase/openshell/invoice-{kind}-provider-profile.yaml'
        assert path in dockerfile and path in build
    template = (ROOT / 'infra/next-phase/invoice-services.bicep').read_text()
    assert "@allowed(['manifest', 'provider'])\nparam credentialMode string = 'manifest'" in template
    assert "{ name: 'INVOICE_CREDENTIAL_MODE', value: credentialMode }" in template
    assert "credential_mode=os.environ.get('INVOICE_CREDENTIAL_MODE','manifest')" in \
        (ROOT / 'src/task_agent/console/invoice_deployed.py').read_text()
