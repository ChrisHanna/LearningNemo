from unittest.mock import MagicMock

import pytest

from task_agent.control.mssql_client import MssqlProcedureClient
from task_agent.control.sql_backend import SqlProcedureUnavailableError


def client(connect, **kwargs):
    return MssqlProcedureClient(server='test.database.windows.net', database='learningnemo',
        client_id='11111111-1111-4111-8111-111111111111', application_name='InvoiceFixture',
        connect=connect, **kwargs)


def ready_connection():
    connection = MagicMock()
    connection.__enter__.return_value = connection
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.description = [('scenario_id',)]
    cursor.fetchall.return_value = [('a'*32,)]
    return connection, cursor


@pytest.mark.asyncio
async def test_cold_connection_recovers_before_scenario_executes_once(monkeypatch):
    from task_agent.control import mssql_client
    pause = MagicMock()
    monkeypatch.setattr(mssql_client.time, 'sleep', pause)
    connection, cursor = ready_connection()
    connect = MagicMock(side_effect=[RuntimeError('database resuming'), connection])
    result = await client(connect, connection_attempts=3, connection_timeout_seconds=30).call(
        'control.usp_create_invoice_scenario', {'scenario_id':'a'*32,'variant':'healthy'})
    assert result == ({'scenario_id':'a'*32},)
    assert connect.call_count == 2
    pause.assert_called_once_with(10)
    cursor.execute.assert_called_once()
    assert connection.autocommit is True


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['execute', 'fetchall', 'exit'])
async def test_uncertain_procedure_is_never_replayed(failure):
    connection, cursor = ready_connection()
    if failure == 'exit':
        connection.__exit__.side_effect = RuntimeError('connection lost after execution')
    else:
        getattr(cursor, failure).side_effect = RuntimeError('outcome uncertain')
    connect = MagicMock(return_value=connection)
    with pytest.raises(SqlProcedureUnavailableError):
        await client(connect, connection_attempts=3).call('control.usp_create_invoice_scenario',
            {'scenario_id':'a'*32,'variant':'healthy'})
    connect.assert_called_once()
    cursor.execute.assert_called_once()


@pytest.mark.asyncio
async def test_connection_recovery_is_bounded_and_opt_in(monkeypatch):
    from task_agent.control import mssql_client
    pause = MagicMock()
    monkeypatch.setattr(mssql_client.time, 'sleep', pause)
    for options, expected in [({}, 1), ({'connection_attempts':3}, 3)]:
        connect = MagicMock(side_effect=RuntimeError('unavailable'))
        with pytest.raises(SqlProcedureUnavailableError):
            await client(connect, **options).call('control.usp_create_invoice_scenario',
                {'scenario_id':'a'*32,'variant':'healthy'})
        assert connect.call_count == expected
    assert pause.call_count == 2


def test_connection_policy_rejects_unbounded_values():
    for options in [{'connection_attempts':0}, {'connection_attempts':4}, {'connection_attempts':True},
                    {'connection_timeout_seconds':0}, {'connection_timeout_seconds':31}]:
        with pytest.raises(ValueError):
            client(MagicMock(), **options)


def test_connection_and_procedure_timeouts_remain_separate(monkeypatch):
    from task_agent.control import mssql_client
    connect = MagicMock(return_value=object())
    monkeypatch.setattr(mssql_client, 'managed_identity_connect', connect)
    instance = client(None, connection_timeout_seconds=30, query_timeout_seconds=15)
    instance._driver_connect(instance._connection_string)
    assert connect.call_args.kwargs['timeout_seconds'] == 30
    assert instance._query_timeout_seconds == 15


@pytest.mark.asyncio
async def test_scenario_proxy_allows_cold_connection_budget_but_sends_once(monkeypatch):
    import httpx
    from task_agent.console import remote_invoice
    original = httpx.AsyncClient
    timeouts, requests = [], []
    def response(request):
        requests.append(request)
        return httpx.Response(201, json={'scenario_id':'a'*32,'source':'dedicated-invoice-simulator'})
    def factory(**kwargs):
        timeouts.append(kwargs['timeout'])
        return original(**kwargs, transport=httpx.MockTransport(response))
    monkeypatch.setattr(remote_invoice.httpx, 'AsyncClient', factory)
    domain = '.internal.jollybeach-503c7ed1.eastus.azurecontainerapps.io'
    proxy = remote_invoice.RemoteInvoiceService('https://ca-nemo-invoice-operator-dev'+domain,
        'https://ca-nemo-invoice-review-dev'+domain)
    await proxy.request('POST','/invoices/scenarios','fixture',{'scenario_id':'a'*32,'variant':'healthy'})
    assert timeouts == [150] and len(requests) == 1
    assert requests[0].url.path == '/invoices/scenarios'