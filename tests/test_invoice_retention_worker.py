import asyncio
import base64
from contextlib import asynccontextmanager
import json
import zlib
from types import SimpleNamespace

from task_agent.console import invoice_retention_worker as worker


async def test_sweep_uses_only_closed_read_and_compilable_fixed_host_script():
    calls = []
    async def call(name, parameters):
        calls.append((name, parameters))
        return ()
    def command(script):
        compile(script.split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0], '<retention-host>', 'exec')
        assert 'apply=False' in script
        return 'INVOICE_RETENTION_Z ' + base64.b64encode(zlib.compress(json.dumps({'status': 'preview', 'deleted': []}).encode())).decode()
    result = await worker.sweep(SimpleNamespace(command=command), SimpleNamespace(call=call))
    assert result['status'] == 'preview'
    assert calls == [('control.usp_read_invoice_retention', {})]


async def test_full_inventory_receipt_fits_azure_output_limit():
    from uuid import uuid4
    expected={'status':'preview','deleted':[],'remaining_count':24,'sandboxes':[
        {'sandbox_id':uuid4().hex,'name':'ip-'+uuid4().hex[:16],'phase':'Stopped','deletable':False,
         'reason':'No verified completed run available for deletion'} for _ in range(24)]}
    assert len(json.dumps(expected))>4096
    async def call(*args):return ()
    def command(script):
        receipt='INVOICE_RETENTION_Z '+base64.b64encode(zlib.compress(json.dumps(expected).encode())).decode()
        assert len(receipt)<3500
        return receipt
    result=await worker.sweep(SimpleNamespace(command=command),SimpleNamespace(call=call))
    assert result['sandboxes']==expected['sandboxes'] and result['remaining_count']==24


async def test_manual_deletion_never_dispatches_a_foreign_or_unverified_sandbox():
    import pytest
    from task_agent.control.operations import OperationDeniedError
    async def call(*args):return ()
    def forbidden(*args):raise AssertionError('host must not be called')
    manager=worker.InvoiceSandboxManager(SimpleNamespace(command=forbidden),SimpleNamespace(call=call),SimpleNamespace(lock=asyncio.Lock()))
    with pytest.raises(OperationDeniedError):await manager.delete('a'*32,'b'*64)


async def test_manager_requires_confirmed_archive_and_exact_deletion(monkeypatch):
    calls=[]
    async def sweep(runtime,client,**options):
        calls.append(options)
        return {'sandboxes':[{'sandbox_id':'a'*32,'phase':'Deleted'}]}
    monkeypatch.setattr(worker,'sweep',sweep)
    manager=worker.InvoiceSandboxManager(None,None,SimpleNamespace(lock=asyncio.Lock()))
    assert await manager.delete('a'*32,'b'*64)=={'sandbox_id':'a'*32,'state':'deleted','evidence_archived':True}
    assert calls==[{'apply':True,'sponsor_hash':'b'*64,'manual_id':'a'*32}]


async def test_pre_run_cleanup_drains_confirmed_batches_before_admission(monkeypatch):
    counts=[18,16,14]
    calls=[]
    async def sweep(runtime,client,**options):
        before=counts[len(calls)];calls.append(before)
        return {'status':'applied','before_count':before,'remaining_count':before-2,'deleted':[str(before),str(before-1)]}
    monkeypatch.setattr(worker,'sweep',sweep)
    manager=worker.InvoiceSandboxManager(None,None,SimpleNamespace(lock=asyncio.Lock()))
    await manager.before_run()
    assert calls==counts


async def test_cleanup_stops_without_progress_and_reserves_last_slot(monkeypatch):
    import pytest
    from task_agent.control.invoice_sandbox import SandboxCapacityError
    calls=[]
    async def sweep(runtime,client,**options):
        calls.append(options)
        return {'status':'applied','before_count':23,'remaining_count':23,'deleted':[]}
    monkeypatch.setattr(worker,'sweep',sweep)
    manager=worker.InvoiceSandboxManager(None,None,SimpleNamespace(lock=asyncio.Lock()))
    with pytest.raises(SandboxCapacityError,match='reserved') as error:await manager.before_run()
    assert error.value.receipt()['reserved_slots']==1
    assert len(calls)==1


async def test_cleanup_never_repeats_after_uncertain_deletion(monkeypatch):
    import pytest
    calls=[]
    async def sweep(*args,**options):
        calls.append(options)
        raise RuntimeError('lost deletion receipt')
    monkeypatch.setattr(worker,'sweep',sweep)
    with pytest.raises(RuntimeError,match='lost deletion'):await worker.cleanup_pressure(None,None)
    assert len(calls)==1


def test_pressure_migration_preserves_success_gate_and_orders_oldest_first():
    from pathlib import Path
    from sqlfluff.core import Linter
    source=(Path(__file__).parents[1]/'infra/next-phase/review-service/015_invoice_retention_pressure.sql').read_text()
    assert not Linter(dialect='tsql').parse_string(source).violations
    assert 'job.SponsorHash AS sponsor_hash' in source
    assert "job.State = 'finished'" in source and 'run.RevokedAt IS NOT NULL' in source
    assert 'ORDER BY stopped.ObservedAt ASC' in source and 'DATEADD(hour, -24' not in source


async def test_monitor_stops_cleanly_and_does_not_duplicate_sweep(monkeypatch):
    called = asyncio.Event()
    calls = []
    async def sweep(runtime, client, *, apply):
        calls.append(apply)
        called.set()
        return {'status': 'disabled', 'deleted': []}
    monkeypatch.setattr(worker, 'sweep', sweep)
    controller = SimpleNamespace(lock=asyncio.Lock())
    manager = worker.InvoiceSandboxManager(None, None, controller)
    manager.demo.start('a'*32,'owner')
    lifespan = worker.retention_lifespan(None, None, controller, manager=manager)
    async with lifespan(None):
        await asyncio.wait_for(called.wait(), 1)
    assert calls == [True]


async def test_completed_run_wakes_monitor_without_waiting_for_timer(monkeypatch):
    observations=asyncio.Queue()
    async def sweep(*args,**options):
        await observations.put(options)
        return {'status':'applied','before_count':18,'remaining_count':18,'deleted':[]}
    monkeypatch.setattr(worker,'sweep',sweep)
    controller=SimpleNamespace(lock=asyncio.Lock())
    manager=worker.InvoiceSandboxManager(None,None,controller)
    manager.demo.start('a'*32,'owner')
    async with worker.retention_lifespan(None,None,controller,manager=manager)(None):
        assert await asyncio.wait_for(observations.get(),1)=={'apply':True}
        manager.notify()
        assert await asyncio.wait_for(observations.get(),1)=={'apply':True}
    assert observations.empty()


async def test_monitor_marks_only_its_locked_maintenance_window(monkeypatch):
    entered=asyncio.Event();release=asyncio.Event()
    controller=SimpleNamespace(lock=asyncio.Lock())
    async def sweep(*args,**options):
        assert controller.lock.locked() and controller.maintenance_active is True
        entered.set();await release.wait()
        return {'status':'disabled','deleted':[]}
    monkeypatch.setattr(worker,'sweep',sweep)
    manager=worker.InvoiceSandboxManager(None,None,controller)
    manager.demo.start('a'*32,'owner')
    async with worker.retention_lifespan(None,None,controller,manager=manager)(None):
        await asyncio.wait_for(entered.wait(),1)
        release.set()
    assert controller.maintenance_active is False and not controller.lock.locked()


def test_operator_commands_compile_and_fit_remote_command_budget():
    import base64
    import importlib.util
    from pathlib import Path
    import sys
    import zlib
    directory = Path(__file__).parents[1] / 'infra/next-phase'
    sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location('retention_config', directory / 'configure_invoice_retention.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for apply in (True, False):
        source = module.sweep_code(apply)
        compile(source, '<remote-sweep>', 'exec')
        assert len(base64.b64encode(zlib.compress(source.encode()))) <= 1700
        for enabled in (True, False):
            script = module.policy_script(enabled, apply)
            compile(script.split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0], '<host-policy>', 'exec')
            assert "'sandbox','delete'" not in script and "'sandbox','stop'" not in script