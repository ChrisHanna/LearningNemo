"""Fixed probes share durable at-most-once SQL admission with agent jobs."""

import json

from task_agent.control.invoice_jobs import InvoiceJobs
from task_agent.control.operations import OperationDeniedError
from task_agent.control.invoice_sandbox import SandboxCapacityError


TARGETS = {'planning': '0'*31+'1', 'execution': '0'*31+'2'}


class InvoiceChallenges:
    def __init__(self, controller, probe, *, on_finished=None):
        self.controller, self.probe = controller, probe
        self.jobs = InvoiceJobs(controller.admission, None)
        self.on_finished = on_finished

    async def enqueue(self, identifier, owner, kind):
        if kind not in TARGETS: raise OperationDeniedError('unknown challenge')
        await self.jobs.enqueue(job_id=identifier, sponsor_hash=owner, kind='planning', target_id=TARGETS[kind])
        return {'challenge_id': identifier}

    async def work(self, identifier, owner):
        job = await self.jobs.status(identifier, owner)
        kind = next((key for key, value in TARGETS.items() if value == job['target_id']), None)
        if kind is None: raise OperationDeniedError('not a challenge job')
        claimed = await self.controller.admission.call('control.usp_claim_invoice_job', {'job_id':identifier,'sponsor_hash':owner})
        if not claimed: return
        if len(claimed) != 1: raise OperationDeniedError('challenge claim unconfirmed')
        async def emit(label):
            await self.jobs.audit(sponsor_hash=owner,run_id=identifier,event={'source':'probe-controller','event_type':'probe-progress','reason':label,'kind':kind})
        try:
            if self.controller.lock.locked() and not getattr(self.controller, 'maintenance_active', False): raise OperationDeniedError('agent run active')
            async with self.controller.lock:
                if getattr(self.controller, 'maintenance', None) is not None:
                    await self.controller.maintenance()
                result = await self.probe(self.controller.runtime, kind, identifier, emit)
            state = 'finished'
        except SandboxCapacityError as error:
            state, result = 'uncertain', {'outcome':'unconfirmed', **error.receipt()}
        except Exception:
            state, result = 'uncertain', {'outcome':'unconfirmed','detail':'Inspect retained sandbox evidence. The challenge was not replayed.'}
        await self.controller.admission.call('control.usp_finish_invoice_job', {'job_id':identifier,'sponsor_hash':owner,'state':state,'result_json':json.dumps(result)})
        if self.on_finished is not None:
            self.on_finished()

    async def status(self, identifier, owner):
        job = await self.jobs.status(identifier, owner)
        kind = next((key for key, value in TARGETS.items() if value == job['target_id']), None)
        if kind is None: raise OperationDeniedError('not a challenge job')
        return {'challenge_id':identifier,'kind':kind,'state':job['state'],'result':job['result'],
            'events':await self.jobs.events(identifier,owner),'created_at':job.get('created_at'),'receipt_retention':'azure-sql'}