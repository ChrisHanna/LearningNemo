from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlfluff.core import Linter

from task_agent.control.invoice_jobs import InvoiceJobs


@pytest.mark.asyncio
async def test_duplicate_worker_does_not_repeat_execution_and_failure_is_uncertain():
    calls, claimed = [], False
    async def call(name, parameters):
        nonlocal claimed
        if name == 'control.usp_claim_invoice_job':
            if claimed:
                return []
            claimed = True
            return [{'kind': 'execution', 'target_id': 'a' * 32, 'plan_hash': 'b' * 64}]
        calls.append((name, parameters))
        return [{'job_id': parameters['job_id']}]
    executions = []
    async def execute(*args, **kwargs):
        executions.append(args)
        raise TimeoutError()
    jobs = InvoiceJobs(SimpleNamespace(call=call), SimpleNamespace(execute=execute))
    await jobs.work('c' * 32, 'd' * 64)
    await jobs.work('c' * 32, 'd' * 64)
    assert len(executions) == 1 and calls[0][1]['state'] == 'uncertain'


def test_job_sql_parses_and_never_reclaims_running_jobs():
    source = (Path(__file__).parents[1] / 'infra/next-phase/review-service/010_invoice_jobs.sql').read_text()
    assert not Linter(dialect='tsql').parse_string(source).violations
    assert "AND State = 'queued'" in source
    assert "THEN 'uncertain'" in source


async def test_capacity_failure_preserves_specific_reason_without_replay():
    import json
    from task_agent.control.invoice_sandbox import SandboxCapacityError
    calls=[]
    async def call(name,parameters):
        calls.append((name,parameters))
        if name=='control.usp_claim_invoice_job':return ({'kind':'planning','target_id':'a'*32},)
        return ({'job_id':parameters['job_id']},)
    async def analyze(*args,**kwargs):raise SandboxCapacityError(24,24)
    await InvoiceJobs(SimpleNamespace(call=call),SimpleNamespace(analyze=analyze)).work('b'*32,'c'*64)
    assert len(calls)==2 and calls[-1][1]['state']=='uncertain'
    result=json.loads(calls[-1][1]['result_json'])
    assert result['reason']=='sandbox-capacity' and result['retained_sandboxes']==24
    assert result['retained_limit']==24 and result['sandbox_created'] is False and result['agent_started'] is False


@pytest.mark.parametrize('persist_fails',[False,True])
async def test_cleanup_notification_follows_durable_completion(persist_fails):
    calls=[]
    async def call(name,values):
        calls.append(name)
        if name=='control.usp_claim_invoice_job':return [{'kind':'planning','target_id':'a'*32}]
        if persist_fails:raise RuntimeError('persistence unconfirmed')
        return [{'job_id':values['job_id']}]
    async def analyze(*args,**kwargs):return {'outcome':'no-change'}
    jobs=InvoiceJobs(SimpleNamespace(call=call),SimpleNamespace(analyze=analyze),on_finished=lambda:calls.append('cleanup-notified'))
    if persist_fails:
        with pytest.raises(RuntimeError):await jobs.work('b'*32,'c'*64)
        assert 'cleanup-notified' not in calls
    else:
        await jobs.work('b'*32,'c'*64)
        assert calls[-2:]==['control.usp_finish_invoice_job','cleanup-notified']