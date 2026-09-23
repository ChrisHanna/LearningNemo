"""Bounded stopped-run retention, independently gated by root-owned host policy."""

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import zlib

from task_agent.console.invoice_remote_runtime import HOST_PREFIX
from task_agent.control.invoice_retention import retention_candidates
from task_agent.control.operations import OperationDeniedError
from task_agent.control.invoice_sandbox import SandboxCapacityError
from task_agent.console.invoice_demo_session import InvoiceDemoSession


async def sweep(runtime, client, *, apply=False, sponsor_hash=None, manual_id=None):
    candidates, retained = await retention_candidates(client, pressure=True, sponsor_hash=sponsor_hash)
    if manual_id is not None and not any(item['sandbox_id'] == manual_id for item in candidates):
        raise OperationDeniedError('Owned, verified completed sandbox required for deletion')
    payload = json.dumps(candidates, separators=(',', ':')).encode()
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError('retention evidence exceeds budget')
    encoded = base64.b64encode(zlib.compress(payload)).decode()
    source = Path(__file__).with_name('invoice_retention_host.py').read_text()
    script = HOST_PREFIX + source + f"\nimport zlib\ncandidates=json.loads(zlib.decompress(base64.b64decode({encoded!r})))\nresult=retention_sweep(candidates,cli,apply={apply!r},pressure=True,manual_id={manual_id!r})\nreceipt=base64.b64encode(zlib.compress(json.dumps(result,separators=(',',':')).encode())).decode()\nassert len(receipt)<3500, 'retention receipt exceeds transport budget'\nprint('INVOICE_RETENTION_Z '+receipt)\nPY\n"
    if len(script.encode()) > 200000:
        raise ValueError('retention command exceeds budget')
    output = await asyncio.to_thread(runtime.command, script)
    records = []
    for line in output.splitlines():
        if line.startswith('INVOICE_RETENTION_Z '):
            receipt = zlib.decompress(base64.b64decode(line.split(' ', 1)[1], validate=True))
            if len(receipt) > 262144:
                raise RuntimeError('retention receipt exceeds readback budget')
            records.append(json.loads(receipt))
    if len(records) != 1:
        raise RuntimeError('retention outcome unconfirmed; inspect archive receipts without replay')
    return {**records[0], 'rejected_evidence': retained, 'checked_at': datetime.now(UTC).isoformat()}


async def cleanup_pressure(runtime, client):
    deleted = set()
    for _ in range(6):
        result = await sweep(runtime, client, apply=True)
        if result.get('status') != 'applied':
            return result
        before, remaining = result.get('before_count'), result.get('remaining_count')
        batch = result.get('deleted')
        if (type(before) is not int or type(remaining) is not int or not 0 <= remaining <= before or
                not isinstance(batch, list) or len(batch) > 2 or any(not isinstance(identifier, str) for identifier in batch) or
                len(set(batch)) != len(batch) or deleted.intersection(batch) or before - remaining != len(batch)):
            raise RuntimeError('cleanup progress unconfirmed; no further deletion')
        deleted.update(batch)
        if remaining < 14 or not batch:
            return result
    return result


class InvoiceSandboxManager:
    def __init__(self, runtime, client, controller):
        self.runtime, self.client, self.controller = runtime, client, controller
        self.changed = asyncio.Event()
        self.demo = InvoiceDemoSession(notify=self.notify)

    def notify(self):
        self.changed.set()

    async def inventory(self, sponsor_hash):
        async with self.controller.lock:
            self.controller.maintenance_active = True
            try:
                result = await sweep(self.runtime, self.client, sponsor_hash=sponsor_hash)
            finally:
                self.controller.maintenance_active = False
        return self.public(result)

    async def delete(self, sandbox_id, sponsor_hash):
        if self.controller.lock.locked():
            raise OperationDeniedError('Agent work is active; deletion is blocked')
        async with self.controller.lock:
            result = await sweep(self.runtime, self.client, apply=True, sponsor_hash=sponsor_hash, manual_id=sandbox_id)
        receipt = next((item for item in result.get('sandboxes', []) if item['sandbox_id'] == sandbox_id and item['phase'] == 'Deleted'), None)
        if receipt is None:
            raise OperationDeniedError('Deletion not confirmed. Refresh inventory; do not replay the request')
        return {'sandbox_id': sandbox_id, 'state': 'deleted', 'evidence_archived': True}

    async def before_run(self):
        result = await cleanup_pressure(self.runtime, self.client)
        if result.get('status') != 'applied' or type(result.get('remaining_count')) is not int:
            raise OperationDeniedError('Sandbox capacity could not be verified; no new sandbox admitted')
        if result['remaining_count'] >= 23:
            raise SandboxCapacityError(result['remaining_count'], 24, reserved_slots=1)

    @staticmethod
    def public(result):
        keys = ('status','remaining_count','free_slots','free_gib','limit','cleanup_at_count','target_count','sandboxes','checked_at')
        return {'source':'openshell-sandbox-inventory', **{key:result[key] for key in keys if key in result}}


def retention_lifespan(runtime, client, controller, *, manager=None):
    manager = manager or InvoiceSandboxManager(runtime, client, controller)
    @asynccontextmanager
    async def lifespan(app):
        stopping = False
        async def monitor():
            while not stopping:
                manager.changed.clear()
                state = manager.demo.status()['state']
                final = manager.demo.claim_final_cleanup()
                if (state == 'active' and not manager.demo.inflight or final) and not controller.lock.locked():
                    try:
                        async with controller.lock:
                            controller.maintenance_active = True
                            try:
                                result = await cleanup_pressure(runtime, client)
                            finally:
                                controller.maintenance_active = False
                        print('INVOICE_RETENTION_STATUS ' + json.dumps(result), flush=True)
                        if final:
                            manager.demo.finish_cleanup(result.get('status') == 'applied')
                    except Exception as error:
                        print('INVOICE_RETENTION_UNCONFIRMED ' + type(error).__name__, flush=True)
                        if final:
                            manager.demo.finish_cleanup(False)
                elif final:
                    manager.demo.cleanup_claimed = False
                try:
                    await asyncio.wait_for(manager.changed.wait(), timeout=60)
                except TimeoutError:
                    pass
        task = asyncio.create_task(monitor())
        try:
            yield
        finally:
            stopping = True
            manager.notify()
            await task
    return lifespan