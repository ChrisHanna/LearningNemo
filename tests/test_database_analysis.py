from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from task_agent.console.analysis_contract import DiagnosticSnapshot
from task_agent.control.database_analysis import DiagnosticReader, DatabaseAnalysisService


@pytest.mark.asyncio
@pytest.mark.parametrize('fail', [False, True])
async def test_diagnostic_reader_sends_only_get_never_cancels(monkeypatch, fail):
    calls = []
    class Credential:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get_token(self, scope): return SimpleNamespace(token='fixture')
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            calls.append(url)
            if fail: raise httpx.ReadTimeout('fixture')
            return httpx.Response(200, json={'active_query_version':'cycle-unsafe-v1','query_run_states':{'run-fixture':'running'}})
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    from task_agent.control.sql_backend import SqlProcedureUnavailableError
    reader = DiagnosticReader(str(uuid4()), Credential)
    if fail:
        with pytest.raises(SqlProcedureUnavailableError): await reader.read()
    else:
        assert (await reader.read()).query_run_states['run-fixture'] == 'running'
    assert len(calls) == 1 and calls[0].endswith('/v1/diagnostics/current')


@pytest.mark.asyncio
async def test_analysis_only_records_evidence_without_workload_mutation():
    calls = []
    class Client:
        async def call(self, name, parameters):
            import json
            calls.append(name)
            return ({'analysis_id': json.loads(parameters['analysis_json'])['analysis_id']},)
    class Reader:
        async def read(self): return DiagnosticSnapshot(active_query_version='cycle-unsafe-v1', query_run_states={})
    result = await DatabaseAnalysisService(Client(), Reader()).analyze('a'*64)
    assert result.queries_cancelled is False and result.workload_changed is False
    assert calls == ['control.usp_record_readonly_analysis']


def test_readonly_analysis_sql_never_mutates_workload_or_approves():
    from pathlib import Path
    from sqlfluff.core import Linter
    source = (Path(__file__).parents[1] / 'infra/next-phase/review-service/007_readonly_analysis.sql').read_text()
    assert not [str(error) for batch in source.split('\nGO\n') if batch.strip() for error in Linter(dialect='tsql').parse_string(batch).violations]
    for forbidden in ('UPDATE lab.', 'INSERT lab.', 'DELETE ', 'KILL ', 'EXEC ops.', 'usp_issue_approval'):
        assert forbidden not in source
    assert 'RunId IS NOT NULL' in source
    assert 'SponsorHash = @sponsor_hash AND EvidenceHash = @evidence_hash' in source