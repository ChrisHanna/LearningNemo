"""Host-side OpenShell lifecycle adapter; never accepts browser shell commands."""

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import subprocess
from uuid import UUID

import yaml

from task_agent.control.canonical import content_hash
from task_agent.control.invoice_agent import RunManifest, validate_manifest


IMAGE = re.compile(r'^crlearningnemodevgruyrc4qwdvvm\.azurecr\.io/learningnemo/invoice-agent@sha256:[a-f0-9]{64}$')
RUN_ID = re.compile(r'^[a-f0-9]{32}$')


class SandboxCapacityError(RuntimeError):
    def __init__(self, retained, limit, *, reserved_slots=0):
        self.retained, self.limit = retained, limit
        self.reserved_slots = reserved_slots
        reason = f'Sandbox admission paused: {retained} retained / {limit} limit; {reserved_slots} slot reserved.' if reserved_slots else f'Sandbox capacity reached: {retained} retained / {limit} limit.'
        super().__init__(reason + ' No sandbox was created or agent started. Capacity must be resolved before a fresh run.')

    def receipt(self):
        return {'detail': str(self), 'reason': 'sandbox-capacity', 'retained_sandboxes': self.retained,
                'retained_limit': self.limit, 'reserved_slots': self.reserved_slots, 'sandbox_created': False, 'agent_started': False}


def sandbox_name(kind, run_id):
    if kind not in ('planning', 'execution') or not RUN_ID.fullmatch(run_id):
        raise ValueError('invalid sandbox identifier')
    return ('ip-' if kind == 'planning' else 'ix-') + run_id[:16]


class OpenShellInvoiceRuntime:
    def __init__(self, image, policy_directory, *, command=subprocess.run, process=asyncio.create_subprocess_exec):
        if not IMAGE.fullmatch(image):
            raise ValueError('pinned owned invoice agent image required')
        self.image, self.policy_directory, self.command, self.process = image, Path(policy_directory), command, process

    def cli(self, *arguments, timeout=60):
        result = self.command(['openshell', '--gateway', 'openshell', *arguments], capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode:
            raise RuntimeError('OpenShell operation failed; inspect retained host diagnostics')
        return result.stdout

    def stop(self, kind, run_id):
        if kind not in ('planning', 'execution') or not RUN_ID.fullmatch(run_id):
            raise ValueError('invalid sandbox identifier')
        self.cli('sandbox', 'stop', sandbox_name(kind, run_id))

    def prepare(self, kind, run_id, lease_seconds=600):
        if kind not in ('planning', 'execution') or not RUN_ID.fullmatch(run_id) or not 60 <= lease_seconds <= 900:
            raise ValueError('invalid bounded sandbox request')
        name = sandbox_name(kind, run_id)
        inventory = json.loads(self.cli('sandbox', 'list', '--output', 'json'))
        if not isinstance(inventory, list):
            raise RuntimeError('unrecognized OpenShell inventory')
        retained = [item for item in inventory if item.get('name', '').startswith(('invoice-', 'ip-', 'ix-'))]
        if any(item.get('name') == name for item in inventory) or len(retained) >= 8:
            raise RuntimeError('retained sandbox quota or name collision; no sandbox removed')
        if any(item.get('phase') not in ('Stopped', 'Error') for item in retained):
            raise RuntimeError('another invoice sandbox is active')
        policy_path = self.policy_directory / f'invoice-{kind}-policy.yaml'
        expected = yaml.safe_load(policy_path.read_text())
        self.cli('sandbox', 'create', '--name', name, '--from', self.image, '--policy', str(policy_path),
                 '--cpu', '1', '--memory', '2Gi', '--detach', '--', '/bin/sleep', 'infinity', timeout=240)
        try:
            observed = json.loads(self.cli('sandbox', 'get', name, '--output', 'json'))
            actual_policy = yaml.safe_load(self.cli('sandbox', 'get', name, '--policy-only'))
            if observed.get('name') != name or observed.get('phase') != 'Ready' or actual_policy != expected:
                raise RuntimeError('sandbox identity, readiness, or applied policy differs')
            sandbox_id = UUID(observed['id']).hex
            timer = self.command(['systemd-run', '--user', '--unit', 'invoice-expire-' + run_id,
                '--on-active', str(lease_seconds) + 's', 'openshell', '--gateway', 'openshell', 'sandbox', 'stop', name],
                capture_output=True, text=True, timeout=20, check=False)
            if timer.returncode:
                raise RuntimeError('independent sandbox expiry timer was not created')
            return {'run_id': run_id, 'sandbox_id': sandbox_id, 'name': name, 'policy_hash': content_hash(actual_policy), 'image': self.image}
        except Exception:
            self.cli('sandbox', 'stop', name)
            raise

    async def execute(self, manifest: RunManifest, on_event):
        expiry = validate_manifest(manifest)
        name = sandbox_name(manifest.kind, manifest.run_id)
        observed = json.loads(await asyncio.to_thread(self.cli, 'sandbox', 'get', name, '--output', 'json'))
        if observed.get('phase') != 'Ready' or UUID(observed['id']).hex != manifest.sandbox_id:
            raise RuntimeError('prepared sandbox no longer matches run')
        process = await self.process('openshell', '--gateway', 'openshell', 'sandbox', 'exec', '--name', name,
            '--no-tty', '--timeout', '600', '--', '/opt/venv/bin/python', '-m', 'task_agent.control.invoice_agent',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=65536)
        final = None
        sequence = 0
        try:
            private_manifest = manifest.model_dump(mode='json')
            private_manifest['capability'] = manifest.capability.get_secret_value()
            process.stdin.write(json.dumps(private_manifest).encode() + b'\n')
            await process.stdin.drain()
            process.stdin.close()
            async with asyncio.timeout(min(620, (expiry - datetime.now(UTC)).total_seconds())):
                async for line in process.stdout:
                    if len(line) > 65536 or sequence >= 100:
                        raise RuntimeError('bounded agent event output exceeded')
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict) or event.get('run_id') != manifest.run_id or event.get('sandbox_id') != manifest.sandbox_id:
                        continue
                    if event.get('source') != 'agent-runtime' or event.get('actor') != manifest.kind:
                        raise RuntimeError('agent event source differs')
                    if manifest.capability.get_secret_value() in line.decode(errors='replace'):
                        raise RuntimeError('sensitive agent output suppressed')
                    sequence += 1
                    if event.get('sequence') != sequence:
                        raise RuntimeError('agent event sequence incomplete')
                    await on_event({**event, 'received_at': datetime.now(UTC).isoformat(), 'provenance': 'sandbox-reported'})
                    if event.get('event_type') == 'agent-finished':
                        final = event
                code = await process.wait()
            if code != 0 or final is None:
                raise RuntimeError('agent run incomplete; no successful outcome assumed')
            return final
        finally:
            await asyncio.to_thread(self.cli, 'sandbox', 'stop', name)
            if process.returncode is None:
                process.terminate()
                await process.wait()