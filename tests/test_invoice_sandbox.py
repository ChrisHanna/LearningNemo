import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from task_agent.control.invoice_sandbox import OpenShellInvoiceRuntime


POLICIES = Path(__file__).resolve().parents[1] / 'infra/next-phase/openshell'
IMAGE = 'crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/invoice-agent@sha256:' + 'a' * 64


def test_new_sandbox_identity_and_independent_timer_are_required():
    calls = []
    run = 'b' * 32
    name = 'ip-' + run[:16]
    def command(arguments, **kwargs):
        calls.append(arguments)
        if 'list' in arguments:
            result = '[]'
        elif '--policy-only' in arguments:
            result = (POLICIES / 'invoice-planning-policy.yaml').read_text()
        elif 'get' in arguments:
            result = json.dumps({'id': 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'name': name, 'phase': 'Ready'})
        else:
            result = ''
        return SimpleNamespace(returncode=0, stdout=result)
    prepared = OpenShellInvoiceRuntime(IMAGE, POLICIES, command=command).prepare('planning', run)
    assert prepared['sandbox_id'] == 'cccccccccccc4ccc8ccccccccccccccc'
    assert any('systemd-run' in call and '--on-active' in call for call in calls)
    assert not any('delete' in call or 'policy' in call and 'set' in call for call in calls)


def test_role_policies_do_not_overlap_tool_authority():
    policies = {kind: yaml.safe_load((POLICIES / f'invoice-{kind}-policy.yaml').read_text()) for kind in ('planning', 'execution')}
    for kind, policy in policies.items():
        assert policy['filesystem_policy']['include_workdir'] is False
        assert policy['filesystem_policy']['read_write'] == ['/tmp']
        paths = [rule['allow']['path'] for endpoint in next(iter(policy['network_policies'].values()))['endpoints'] for rule in endpoint['rules']]
        assert ('/v2/invoice/tools/execute_step' in paths) == (kind == 'execution')
        assert ('/v2/invoice/tools/invoice_summary' in paths) == (kind == 'planning')


def test_unpinned_image_or_invalid_run_cannot_create_sandbox():
    with pytest.raises(ValueError):
        OpenShellInvoiceRuntime(IMAGE.split('@')[0] + ':latest', POLICIES)
    runtime = OpenShellInvoiceRuntime(IMAGE, POLICIES, command=lambda *args, **kwargs: pytest.fail('must not invoke CLI'))
    with pytest.raises(ValueError):
        runtime.prepare('planning', 'invalid; command')


@pytest.mark.asyncio
async def test_capability_is_transferred_only_via_private_stdin():
    import asyncio
    from test_invoice_agent import manifest
    context = manifest()
    received, commands, events = [], [], []
    output = asyncio.StreamReader()
    output.feed_data((json.dumps({'sequence': 1, 'run_id': context.run_id, 'sandbox_id': context.sandbox_id,
        'source': 'agent-runtime', 'actor': 'planning', 'event_type': 'agent-finished'}) + '\n').encode())
    output.feed_eof()
    class Input:
        def write(self, value): received.append(value)
        async def drain(self): pass
        def close(self): pass
    class Process:
        stdin = Input()
        stdout = output
        returncode = 0
        async def wait(self): return 0
    async def process(*arguments, **kwargs):
        commands.append(arguments)
        return Process()
    def command(arguments, **kwargs):
        commands.append(arguments)
        return SimpleNamespace(returncode=0, stdout=json.dumps({'id': context.sandbox_id, 'phase': 'Ready'}))
    async def record(event): events.append(event)
    runtime = OpenShellInvoiceRuntime(IMAGE, POLICIES, command=command, process=process)
    await runtime.execute(context, record)
    assert json.loads(received[0])['capability'] == context.capability.get_secret_value()
    assert context.capability.get_secret_value() not in str(commands) + str(events)