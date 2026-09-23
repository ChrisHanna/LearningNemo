"""Durable at-most-once job claims; disconnected viewers never replay work."""

import json

from task_agent.control.invoice_sandbox import SandboxCapacityError
from task_agent.control.operations import OperationDeniedError
from task_agent.control.sql_backend import SqlProcedureUnavailableError


class InvoiceJobs:
    def __init__(self, client, controller, *, on_finished=None):
        self.client, self.controller = client, controller
        self.on_finished = on_finished

    async def enqueue(self, *, job_id, sponsor_hash, kind, target_id, plan_hash=None):
        rows = await self.client.call('control.usp_admit_invoice_job', dict(job_id=job_id, sponsor_hash=sponsor_hash,
            kind=kind, target_id=target_id, plan_hash=plan_hash))
        if len(rows) != 1 or rows[0] != {'job_id': job_id}:
            raise SqlProcedureUnavailableError('job admission unconfirmed; keep request ID')
        return {'job_id': job_id}

    async def work(self, job_id, sponsor_hash):
        rows = await self.client.call('control.usp_claim_invoice_job', dict(job_id=job_id, sponsor_hash=sponsor_hash))
        if not rows:
            return
        if len(rows) != 1:
            raise SqlProcedureUnavailableError('invalid job claim')
        job = rows[0]
        try:
            if job['kind'] == 'planning':
                result = await self.controller.analyze(job['target_id'], sponsor_hash=sponsor_hash, run_id=job_id)
            else:
                result = await self.controller.execute(job['target_id'], job['plan_hash'], sponsor_hash=sponsor_hash, run_id=job_id)
            state = 'finished'
        except SandboxCapacityError as error:
            state, result = 'uncertain', error.receipt()
        except Exception:
            state, result = 'uncertain', {'detail': 'Run stopped or outcome unconfirmed. Inspect persisted plan and receipts; no write was replayed.'}
        await self.client.call('control.usp_finish_invoice_job', dict(job_id=job_id, sponsor_hash=sponsor_hash,
            state=state, result_json=json.dumps(result, separators=(',', ':'))))
        if self.on_finished is not None:
            self.on_finished()

    async def status(self, job_id, sponsor_hash):
        rows = await self.client.call('control.usp_get_invoice_job', dict(job_id=job_id, sponsor_hash=sponsor_hash))
        if not rows:
            raise OperationDeniedError('job not owned')
        row = dict(rows[0])
        row['result'] = json.loads(row.pop('result_json')) if row.get('result_json') else None
        row.pop('result_json', None)
        receipt = row.pop('sandbox_test_json', None)
        if receipt is not None:
            row['sandbox_test'] = json.loads(receipt)
        return row

    async def request_sandbox_test(self, job_id, sponsor_hash, sandbox_id):
        rows = await self.client.call('control.usp_request_invoice_sandbox_test',
            {'job_id': job_id, 'sponsor_hash': sponsor_hash, 'sandbox_id': sandbox_id})
        if len(rows) != 1 or rows[0] != {'job_id': job_id, 'sandbox_id': sandbox_id}:
            raise SqlProcedureUnavailableError('sandbox test request unconfirmed; read the existing job, do not replay')
        return {'job_id': job_id, 'sandbox_id': sandbox_id, 'state': 'requested'}

    async def audit(self, *, sponsor_hash, run_id, event):
        fields = {'source','event_type','sandbox_id','policy_hash','kind','actor','tool','outcome','reason','http_status','uid','executed_steps','independently_verified','provenance','step_id','operation','target','revision','receipt_hash','orders','active_invoices','duplicate_invoices','expected_cents','reported_cents'}
        safe = {key: value for key, value in event.items() if key in fields and type(value) in (str, bool, int)}
        for key, value in safe.items():
            if isinstance(value, str):
                safe[key] = ''.join(character for character in value[:160] if 32 <= ord(character) <= 126)
        await self.client.call('control.usp_append_invoice_activity', dict(job_id=run_id, sponsor_hash=sponsor_hash,
            event_json=json.dumps(safe, separators=(',', ':'))))

    async def events(self, job_id, sponsor_hash, after=0):
        rows = await self.client.call('control.usp_read_invoice_activity', dict(job_id=job_id, sponsor_hash=sponsor_hash, after=after))
        return [{**json.loads(row['event_json']), 'sequence': row['sequence'], 'observed_at': row['observed_at']} for row in rows]