import asyncio
from types import SimpleNamespace

import pytest

from task_agent.control.invoice_challenges import InvoiceChallenges
from task_agent.control.operations import OperationDeniedError
from task_agent.console.invoice_probes import investigation_confirmed, investigation_script, probe_script, run_probe, run_prepared_probe


@pytest.mark.asyncio
async def test_probe_jobs_do_not_replay_and_are_owner_bound(tmp_path):
    calls = []
    records = {}
    async def call(procedure, values):
        identifier=values['job_id']
        if procedure=='control.usp_admit_invoice_job':
            if identifier in records and records[identifier]['sponsor_hash'] != values['sponsor_hash']: raise OperationDeniedError('owner differs')
            records.setdefault(identifier,{**values,'state':'queued','result_json':None})
            return [{'job_id':identifier}]
        record=records[identifier]
        if record['sponsor_hash'] != values['sponsor_hash']: raise OperationDeniedError('owner differs')
        if procedure=='control.usp_get_invoice_job': return [record.copy()]
        if procedure=='control.usp_claim_invoice_job':
            if record['state']!='queued': return []
            record['state']='running'; return [record.copy()]
        if procedure=='control.usp_finish_invoice_job': record.update(values); return []
        return []
    async def probe(runtime, kind, identifier, emit):
        calls.append(identifier)
        await emit('Fixed probe finished')
        return {'outcome':'denied'}
    jobs = InvoiceChallenges(SimpleNamespace(lock=asyncio.Lock(),runtime=None,admission=SimpleNamespace(call=call)), probe)
    await jobs.enqueue('a'*32,'owner','planning')
    assert await jobs.enqueue('a'*32,'owner','planning') == {'challenge_id':'a'*32}
    with pytest.raises(OperationDeniedError): await jobs.enqueue('a'*32,'other','planning')
    await jobs.work('a'*32,'owner')
    await jobs.work('a'*32,'owner')
    assert calls == ['a'*32]
    assert (await jobs.status('a'*32,'owner'))['result']['outcome'] == 'denied'
    with pytest.raises(OperationDeniedError): await jobs.status('a'*32,'other')


async def test_challenge_checks_capacity_before_probe_and_notifies_after_persistence():
    import json
    order=[];events=[]
    async def call(procedure,values):
        order.append(procedure)
        if procedure=='control.usp_get_invoice_job':return [{'job_id':'a'*32,'target_id':'0'*31+'1','state':'queued','result_json':None}]
        if procedure=='control.usp_claim_invoice_job':return [{'kind':'planning'}]
        if procedure=='control.usp_append_invoice_activity':events.append(json.loads(values['event_json']))
        return []
    async def maintenance():order.append('capacity-check')
    async def probe(_runtime,_kind,_identifier,emit):
        order.append('probe')
        await emit('Executing inside bound sandbox',event_type='investigation-started',sandbox_id='b'*32,uid=998)
        return {'outcome':'denied'}
    controller=SimpleNamespace(lock=asyncio.Lock(),runtime=None,admission=SimpleNamespace(call=call),maintenance=maintenance)
    jobs=InvoiceChallenges(controller,probe,on_finished=lambda:order.append('cleanup-notified'))
    await jobs.work('a'*32,'owner')
    assert order[-5:]==['capacity-check','probe','control.usp_append_invoice_activity','control.usp_finish_invoice_job','cleanup-notified']
    assert events==[{'source':'workspace-controller','event_type':'investigation-started','reason':'Executing inside bound sandbox','kind':'planning','sandbox_id':'b'*32,'uid':998}]


@pytest.mark.asyncio
async def test_live_investigation_executes_in_prepared_sandbox_and_records_lifecycle():
    import json
    events=[];stopped=[]
    run_id='a'*32;sandbox_id='b'*32;policy_hash='c'*64
    receipt={'run_id':run_id,'sandbox_id':sandbox_id,'scenario':'query-draft','uid':998,
        'outcome':'allowed','operation':'generate-read-query','path':'/tmp/investigation.sql',
        'query_class':'SELECT','query_executed':False,'statement_hash':'d'*64}
    def command(script,*_args):
        if 'PROBE_STOP_CONFIRMED' in script:return 'PROBE_STOP_CONFIRMED'
        assert "cli('sandbox','exec'" in script
        return 'INVESTIGATION_RECEIPT '+json.dumps(receipt)
    runtime=SimpleNamespace(prepare=lambda *args:{'run_id':run_id,'sandbox_id':sandbox_id,'policy_hash':policy_hash},
        command=command,stop=lambda *args:stopped.append(args))
    async def emit(reason,**details):events.append({'reason':reason,**details})
    result=await run_probe(runtime,'query-draft',run_id,emit)
    assert result['executed_in_sandbox'] is True and result['sandbox_runtime']=='OpenShell MicroVM'
    assert result['sandbox_executor']=='/opt/venv/bin/python'
    assert result['sandbox_stopped'] is True and result['sandbox_retained'] is True
    assert result['sandbox_id']==sandbox_id and result['policy_hash']==policy_hash and result['uid']==998
    assert [event['event_type'] for event in events]==['preparing-sandbox','sandbox-bound','investigation-started','investigation-recorded','sandbox-stopped']
    assert events[1]['sandbox_id']==sandbox_id and events[1]['policy_hash']==policy_hash
    assert events[3]['uid']==998 and events[3]['outcome']=='allowed'
    assert stopped==[('planning',run_id)]


def test_probe_script_has_fixed_destinations_and_no_credentials():
    for kind in ('planning','execution'):
        script = probe_script(kind,'a'*32,'b'*32)
        compile(script.split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0],'<probe>','exec')
        assert 'Authorization' not in script and 'DENIED' in script and 'dns resolution failed' in script
        assert "'max_retries'" not in script
        assert script.index('if receipt.exists():') < script.index("cli('sandbox','exec'")
        assert "os.O_EXCL" in script


@pytest.mark.parametrize('scenario', ['query-draft', 'allowed-file', 'denied-file', 'sql-denied', 'write-app-denied', 'approved-api', 'external-api-denied', 'symlink-escape-denied'])
def test_investigation_scripts_are_bounded_and_contain_no_credentials(scenario):
    options = {'sql_host': 'sql-learningnemo-dev.database.windows.net'} if scenario == 'sql-denied' else {}
    script = investigation_script(scenario, 'a'*32, 'b'*32, **options)
    compile(script.split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0], '<investigation>', 'exec')
    assert 'Authorization' not in script and 'password' not in script.casefold()
    assert script.index('if receipt.exists():') < script.index("cli('sandbox','exec'")
    assert "os.O_EXCL" in script


def test_investigation_receipts_fail_closed():
    digest = 'a' * 64
    assert investigation_confirmed('query-draft', {'outcome':'allowed','path':'/tmp/investigation.sql','query_class':'SELECT','query_executed':False,'statement_hash':digest})
    assert not investigation_confirmed('query-draft', {'outcome':'allowed','path':'/tmp/investigation.sql','query_class':'SELECT','query_executed':True,'statement_hash':digest})
    assert investigation_confirmed('allowed-file', {'outcome':'allowed','path':'/app/configs/invoice-planning.yml','content_hash':digest})
    assert not investigation_confirmed('denied-file', {'outcome':'allowed','path':'/boundary/private-investigation.txt','error_category':'filesystem-policy-failed'})
    sql_host = 'sql-learningnemo-dev.database.windows.net'
    sql = {'outcome':'policy-denied','destination':sql_host,'destination_port':1433,'error_category':'network-policy-denied','database_capability_issued':False,'denial_evidence':['OCSF CONNECT DENIED '+sql_host+' 1433']}
    assert investigation_confirmed('sql-denied', sql)
    assert not investigation_confirmed('sql-denied', {**sql, 'outcome':'unconfirmed','error_category':'network-timeout'})
    assert not investigation_confirmed('sql-denied', {**sql, 'denial_evidence':['OCSF CONNECT DENIED unrelated.example 1433']})
    assert investigation_confirmed('write-app-denied', {'outcome':'policy-denied','path':'/app/investigation-write-attempt.txt','error_category':'filesystem-policy-denied'})
    assert not investigation_confirmed('write-app-denied', {'outcome':'unexpectedly-allowed','path':'/app/investigation-write-attempt.txt','error_category':'filesystem-policy-failed'})
    approved = {'outcome':'allowed','destination':'ca-nemo-invoice-planning-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io','destination_port':443,'method':'POST','route':'/v2/invoice/tools/invoice_summary','http_status':401,'credential_issued':False}
    assert investigation_confirmed('approved-api', approved)
    assert not investigation_confirmed('approved-api', {**approved, 'http_status':200})
    external = {'outcome':'policy-denied','destination':'example.com','destination_port':443,'error_category':'network-policy-denied','credential_issued':False,'denial_evidence':['OCSF CONNECT DENIED example.com 443']}
    assert investigation_confirmed('external-api-denied', external)
    assert not investigation_confirmed('external-api-denied', {**external, 'denial_evidence':[]})
    assert not investigation_confirmed('external-api-denied', {**external, 'denial_evidence':['OCSF CONNECT DENIED unrelated.example 443']})
    assert not investigation_confirmed('external-api-denied', {**external, 'denial_evidence':[None]})
    symlink = {'outcome':'policy-denied','path':'/tmp/private-investigation-link','target':'/boundary/private-investigation.txt','link_created':True,'link_removed':True,'error_category':'filesystem-policy-denied'}
    assert investigation_confirmed('symlink-escape-denied', symlink)
    assert not investigation_confirmed('symlink-escape-denied', {**symlink, 'link_removed':False})


@pytest.mark.asyncio
async def test_failed_probe_still_stops_sandbox():
    stopped = []
    runtime=SimpleNamespace(prepare=lambda *args:{'sandbox_id':'b'*32,'policy_hash':'c'*64},command=lambda script,*args:'PROBE_STOP_CONFIRMED' if "record=json.loads(cli('sandbox','get'" in script else 'no receipt',stop=lambda *args:stopped.append(args))
    async def emit(_label, **_details): pass
    with pytest.raises(RuntimeError): await run_probe(runtime,'planning','a'*32,emit)
    assert stopped == [('planning','a'*32)]


def test_live_probe_verifier_command_is_bounded(monkeypatch):
    from pathlib import Path
    import base64,zlib
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'infra/next-phase'))
    from verify_conference_probes import code
    source=code('planning','a'*32,'b'*64)
    compile(source,'<live-probe>','exec')
    assert len(base64.b64encode(zlib.compress(source.encode()))) < 1700


@pytest.mark.asyncio
@pytest.mark.parametrize('evidence,status', [([],403),(['policy denial'],0)])
async def test_missing_denial_evidence_or_transport_failure_never_passes(evidence,status):
    import json
    async def emit(_label, **_details): pass
    def command(script,*args):
        if 'PROBE_STOP_CONFIRMED' in script: return 'PROBE_STOP_CONFIRMED'
        return 'PROBE_RESULT '+json.dumps({'run_id':'a'*32,'sandbox_id':'b'*32,'uid':998,
            'requests':[{'tool':'invoice_summary','status':401},{'tool':'execute_step','status':status}],'denial_evidence':evidence})
    runtime=SimpleNamespace(prepare=lambda *args:{'sandbox_id':'b'*32,'policy_hash':'c'*64},command=command,stop=lambda *args:None)
    result=await run_probe(runtime,'planning','a'*32,emit)
    assert result['outcome']=='unconfirmed'
    assert result['executed_in_sandbox'] is True
    assert result['sandbox_runtime']=='OpenShell MicroVM'
    assert result['sandbox_executor']=='/opt/venv/bin/python'
    assert result['sandbox_stopped'] is True and result['sandbox_retained'] is True


@pytest.mark.parametrize('kind,control,forbidden', [('planning','invoice_summary','execute_step'), ('execution','execute_step','invoice_summary')])
async def test_bound_probe_never_creates_restarts_or_stops_a_sandbox(kind, control, forbidden):
    import json
    from task_agent.control.invoice_sandbox import sandbox_name
    commands = []
    prepared = {'run_id': 'a'*32, 'sandbox_id': 'b'*32, 'name': sandbox_name(kind,'a'*32), 'policy_hash': 'c'*64}
    def command(script, timeout):
        commands.append(script)
        compile(script.split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0], '<bound-probe>', 'exec')
        assert "record['phase']=='Ready'" in script and "'exit_code':0" in script
        assert 'Authorization' not in script
        assert all("cli('sandbox','"+verb+"'" not in script for verb in ('create','start','stop','delete'))
        return 'PROBE_RESULT '+json.dumps({'run_id':'a'*32,'sandbox_id':'b'*32,'uid':998,
            'requests':[{'tool':control,'status':401},{'tool':forbidden,'status':403}],
            'denial_evidence':['OCSF DENIED '+forbidden]})
    result = await run_prepared_probe(SimpleNamespace(command=command),kind,'a'*32,prepared)
    assert result['outcome'] == 'denied' and result['scope'] == 'same-agent-sandbox'
    assert result['sandbox_stopped'] is False and result['probe_capability_issued'] is False
    assert len(commands) == 1


async def test_bound_probe_rejects_a_different_prepared_run_before_dispatch():
    def forbidden(*args): pytest.fail('must not dispatch')
    with pytest.raises(ValueError, match='exact agent run'):
        await run_prepared_probe(SimpleNamespace(command=forbidden),'planning','a'*32,{'run_id':'b'*32})