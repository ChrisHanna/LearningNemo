from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from task_agent.control.initiation import TrustedIncidentInitiator
from task_agent.control.models import OperationReceipt
from task_agent.control.operations import OperationDeniedError


NOW = datetime(2026, 9, 15, 23, tzinfo=UTC)


def test_initiation_sql_parses_and_has_durable_admission_guards():
    from pathlib import Path
    from sqlfluff.core import Linter
    source = (Path(__file__).parents[1] / 'infra/next-phase/review-service/004_incident_initiation.sql').read_text()
    linter = Linter(dialect='tsql')
    assert not [str(error) for batch in source.split('\nGO\n') if batch.strip() for error in linter.parse_string(batch).violations]
    assert 'PRIMARY KEY (SponsorHash, RequestId)' in source and 'sys.sp_getapplock' in source
    assert 'WatchdogDeadline > @now_utc' in source
    assert 'usp_issue_approval' not in source and 'DELETE ' not in source


class Client:
    def __init__(self, state='admitted'):
        self.state, self.calls = state, []

    async def call(self, procedure, parameters):
        self.calls.append((procedure, parameters))
        if procedure == 'control.usp_begin_human_investigation':
            return ({'state': self.state, 'plan_id': parameters['plan_id']},)
        import json
        plan = json.loads(parameters['plan_json'])
        return ({'state': 'draft', 'plan_id': plan['plan_id'], 'plan_hash': plan['plan_hash']},)


class Workers:
    def __init__(self, valid=True):
        self.calls, self.valid = [], valid

    async def investigate(self, *, run_id):
        self.calls.append(run_id)
        return ({'active_query_version': 'cycle-unsafe-v1' if self.valid else 'other', 'query_run_states': {run_id: 'running'}},
            OperationReceipt(operation_id='contain.cancel-owned-query-v1', result_code='owned_query_cancelled', result={'run_id': run_id, 'state': 'cancelled'}))


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['admitted', 'recorded', 'running', 'failed'])
async def test_initiation_is_sponsor_bound_and_stops_at_draft(state):
    client, workers = Client(state), Workers()
    initiator = TrustedIncidentInitiator(client, workers, clock=lambda: NOW, expires_at=NOW + timedelta(hours=1))
    if state in ('running', 'failed'):
        with pytest.raises(OperationDeniedError):
            await initiator.start(sponsor_hash='a' * 64, request_id=str(uuid4()))
    else:
        result = await initiator.start(sponsor_hash='a' * 64, request_id=str(uuid4()))
        assert result['state'] == 'draft'
    assert len(workers.calls) == (1 if state == 'admitted' else 0)
    assert client.calls[0][1]['sponsor_hash'] == 'a' * 64
    assert all(name in ('control.usp_begin_human_investigation', 'control.usp_record_human_investigation') for name, _ in client.calls)


@pytest.mark.asyncio
async def test_unmatched_evidence_cannot_become_a_plan():
    client = Client()
    initiator = TrustedIncidentInitiator(client, Workers(False), clock=lambda: NOW, expires_at=NOW + timedelta(hours=1))
    with pytest.raises(OperationDeniedError):
        await initiator.start(sponsor_hash='a' * 64, request_id=str(uuid4()))
    assert len(client.calls) == 1


@pytest.mark.parametrize('persona,scope,status', [('operator', True, 403), ('operator', False, 403), ('approver', True, 403), ('reader', True, 403)])
def test_private_start_uses_only_verified_sponsor(persona, scope, status):
    from fastapi.testclient import TestClient
    from task_agent.console.incident_service import create_incident_app
    from task_agent.console.review_service import ReviewIdentity
    class Verifier:
        async def verify(self, token):
            return ReviewIdentity(persona=persona, subject_hash='a' * 64, scopes={'tasks.execute'} if scope else set())
    class Initiator:
        calls = []
        async def start(self, **kwargs):
            self.calls.append(kwargs)
            return {'state': 'draft'}
    initiator = Initiator()
    client = TestClient(create_incident_app(None, Verifier(), initiator=initiator))
    body = {'request_id': str(uuid4())}
    headers = {'Authorization': 'Bearer fixture'}
    assert client.post('/incidents/start', json=body, headers=headers).status_code == status
    assert initiator.calls == []
    assert client.post('/incidents/start', json={**body, 'sponsor_hash': 'b' * 64}, headers=headers).status_code in (403, 422)


@pytest.mark.asyncio
async def test_diagnostic_failure_still_cancels_only_its_owned_query():
    from task_agent.control.investigation_transport import ManagedInvestigationWorkers
    from task_agent.control.sql_backend import SqlProcedureUnavailableError
    workers = ManagedInvestigationWorkers(diagnostic_audience=str(uuid4()), query_audience=str(uuid4()), credential=None)
    calls = []
    async def request(service, method, path, body=None):
        calls.append((service, method, path, body))
        if path == '/readyz': return {'status': 'ready'}
        if path.endswith('/start'): return {'result_code': 'owned_query_started', 'result': {'run_id': 'run-test', 'state': 'running'}}
        if path.endswith('/current'): raise SqlProcedureUnavailableError('diagnostic unavailable')
        return {'operation_id': 'contain.cancel-owned-query-v1', 'result_code': 'owned_query_cancelled', 'result': {'run_id': 'run-test', 'state': 'cancelled'}}
    workers.request = request
    with pytest.raises(SqlProcedureUnavailableError):
        await workers.investigate(run_id='run-test')
    assert calls[-1] == ('query-runner', 'POST', '/v1/query-runs/cancel', {'run_id': 'run-test'})
    assert sum(path.endswith('/start') for _, _, path, _ in calls) == 1