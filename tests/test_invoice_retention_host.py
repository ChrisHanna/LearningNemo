from copy import deepcopy
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from uuid import UUID
from datetime import timedelta

import pytest

from task_agent.console.invoice_retention_host import RETENTION_POLICY, retention_sweep
from task_agent.control.invoice_retention import retention_candidate
from test_invoice_retention import candidate_record


def host(tmp_path):
    candidate = retention_candidate(candidate_record())
    record = {'id': str(UUID(candidate['sandbox_id'])), 'name': candidate['name'], 'phase': 'Stopped'}
    inventory = [record, {'id': str(UUID('f' * 32)), 'name': 'original-retained', 'phase': 'Stopped'}]
    locations = {name: tmp_path / name for name in ('runs', 'policies', 'sandboxes', 'archives')}
    for path in locations.values(): path.mkdir(mode=0o700)
    run = locations['runs'] / candidate['run_id']
    run.mkdir(mode=0o700)
    files = {'events.jsonl': json.dumps({'source': 'agent-runtime', 'run_id': candidate['run_id'], 'sandbox_id': candidate['sandbox_id'], 'event_type': 'agent-finished'}),
             'error.log': '', 'exit.json': '{"exit_code":0}', 'launch-receipt.json': json.dumps({'sandbox_id': candidate['sandbox_id']})}
    for name, data in files.items(): (run / name).write_text(data)
    (locations['policies'] / (candidate['run_id'] + '.yaml')).write_text('version: 1')
    sandbox = locations['sandboxes'] / record['id']
    sandbox.mkdir()
    for name in ('rootfs-console.log', 'gvproxy.log', 'image-reference', 'image-identity', 'sandbox.pb', 'stopped'):
        (sandbox / name).write_text('fixture')
    stopped = datetime.fromisoformat(candidate['stopped_at']).timestamp()
    os.utime(sandbox / 'stopped', (stopped, stopped))
    policy = tmp_path / 'retention.json'
    policy.write_text(json.dumps(RETENTION_POLICY))
    calls = []
    def cli(*args):
        calls.append(args)
        if args[:2] == ('sandbox', 'list'): return json.dumps(inventory)
        if args[:2] == ('sandbox', 'get'):
            return 'version: 1' if '--policy-only' in args else json.dumps(next(item for item in inventory if item['name'] == args[2]))
        assert args == ('sandbox', 'delete', candidate['name'])
        archive = locations['archives'] / candidate['run_id']
        assert (archive / 'manifest.json').is_file() and (archive / 'delete-attempt.json').is_file()
        inventory.remove(record)
        return ''
    options = {**locations, 'policy': policy, 'lock': tmp_path / 'lifecycle.lock', 'owner': os.getuid()}
    return candidate, inventory, calls, cli, options


def test_preview_never_deletes_or_creates_archives(tmp_path):
    candidate, inventory, calls, cli, options = host(tmp_path)
    result = retention_sweep([candidate], cli, **options)
    assert result['eligible'] == [candidate['run_id']] and len(inventory) == 2
    assert list(options['archives'].iterdir()) == []
    assert not any(call[:2] == ('sandbox', 'delete') for call in calls)


def test_pressure_candidate_keeps_success_and_revocation_requirements():
    record = candidate_record()
    now = datetime.now(UTC)
    record['stopped_at'] = (now - timedelta(minutes=2)).isoformat()
    record['revoked_at'] = (now - timedelta(minutes=3)).isoformat()
    with pytest.raises(ValueError): retention_candidate(record, now)
    assert retention_candidate(record, now, minimum_age_hours=0)['run_id'] == record['run_id']
    record['state'] = 'uncertain'
    with pytest.raises(ValueError): retention_candidate(record, now, minimum_age_hours=0)


def test_pressure_deletes_recent_stopped_run_but_never_below_target(tmp_path):
    candidate, inventory, calls, cli, options = host(tmp_path)
    now = datetime.now(UTC)
    candidate['stopped_at'] = (now - timedelta(minutes=2)).isoformat()
    stopped = options['sandboxes'] / str(UUID(candidate['sandbox_id'])) / 'stopped'
    os.utime(stopped, (now.timestamp()-120, now.timestamp()-120))
    preview = retention_sweep([candidate], cli, pressure=True, **options)
    assert preview['sandboxes'][0]['deletable'] is True
    assert not retention_sweep([candidate], cli, apply=True, pressure=True, **options)['deleted']
    inventory.extend({'id': str(UUID(int=number)), 'name': 'protected-'+str(number), 'phase':'Stopped'} for number in range(1,13))
    result = retention_sweep([candidate], cli, apply=True, pressure=True, **options)
    assert result['deleted'] == [candidate['run_id']] and len(inventory) == 13


def test_manual_delete_requires_exact_eligible_id_and_does_not_touch_neighbors(tmp_path):
    candidate, inventory, calls, cli, options = host(tmp_path)
    result = retention_sweep([candidate], cli, apply=True, manual_id='f'*32, **options)
    assert not result['deleted'] and len(inventory)==2
    result = retention_sweep([candidate], cli, apply=True, manual_id=candidate['sandbox_id'], **options)
    assert result['deleted']==[candidate['run_id']] and len(inventory)==1


def test_pressure_policy_never_deletes_while_any_sandbox_is_active(tmp_path):
    candidate, inventory, calls, cli, options = host(tmp_path)
    inventory[1]['phase']='Ready'
    result = retention_sweep([candidate], cli, apply=True, pressure=True, **options)
    assert result['status']=='active-sandbox' and result['remaining_count']==2
    assert all(not item['deletable'] for item in result['sandboxes'])
    assert not any(call[:2]==('sandbox','delete') for call in calls)


def test_below_pressure_threshold_a_fresh_host_stop_still_blocks_ttl_cleanup(tmp_path):
    candidate, inventory, calls, cli, options = host(tmp_path)
    stopped=options['sandboxes']/str(UUID(candidate['sandbox_id']))/'stopped'
    os.utime(stopped,None)
    result=retention_sweep([candidate],cli,apply=True,pressure=True,**options)
    assert result['deleted']==[] and result['before_count']==2


@pytest.mark.parametrize('initial_count,deleted_count', [(14,1),(16,2)])
def test_pressure_cleanup_selects_oldest_first_and_honors_batch_limit(tmp_path, initial_count, deleted_count):
    candidate, inventory, calls, cli, options = host(tmp_path)
    candidates=[]
    import shutil
    for offset in range(3):
        copied=deepcopy(candidate)
        copied['run_id']=str(offset+1)*32;copied['sandbox_id']=str(offset+4)*32;copied['name']='ip-'+copied['run_id'][:16]
        copied['stopped_at']=(datetime.now(UTC)-timedelta(hours=offset+1)).isoformat()
        copied['evidence'].update(run_id=copied['run_id'],sandbox_id=copied['sandbox_id'])
        shutil.copytree(options['runs']/candidate['run_id'],options['runs']/copied['run_id'])
        directory=options['runs']/copied['run_id']
        (directory/'events.jsonl').write_text(json.dumps({'source':'agent-runtime','run_id':copied['run_id'],'sandbox_id':copied['sandbox_id'],'event_type':'agent-finished'}))
        (directory/'launch-receipt.json').write_text(json.dumps({'sandbox_id':copied['sandbox_id']}))
        shutil.copytree(options['sandboxes']/str(UUID(candidate['sandbox_id'])),options['sandboxes']/str(UUID(copied['sandbox_id'])))
        (options['policies']/(copied['run_id']+'.yaml')).write_text('version: 1')
        inventory.append({'id':str(UUID(copied['sandbox_id'])),'name':copied['name'],'phase':'Stopped'})
        candidates.append(copied)
    inventory.extend({'id':str(UUID(int=number)), 'name':'protected-'+str(number),'phase':'Stopped'} for number in range(1,initial_count-4))
    def delete_cli(*args):
        if args[:2]==('sandbox','delete'):
            record=next(item for item in inventory if item['name']==args[2]);inventory.remove(record);return ''
        return cli(*args)
    result=retention_sweep(candidates,delete_cli,apply=True,pressure=True,**options)
    assert result['deleted']==[item['run_id'] for item in reversed(candidates)][:deleted_count]
    assert len(inventory)==initial_count-deleted_count


def test_archive_precedes_exact_delete_and_every_other_sandbox_is_preserved(tmp_path):
    candidate, inventory, calls, cli, options = host(tmp_path)
    result = retention_sweep([candidate], cli, apply=True, **options)
    assert result['deleted'] == [candidate['run_id']]
    assert inventory[0]['name'] == 'original-retained'
    archive = options['archives'] / candidate['run_id']
    assert (archive / 'deleted.json').is_file()
    assert json.loads((archive / 'sql-evidence.json').read_text()) == candidate
    assert (options['runs'] / candidate['run_id'] / 'events.jsonl').is_file()
    assert all(path.stat().st_mode & 0o077 == 0 for path in archive.iterdir())


@pytest.mark.parametrize('fault', ['running', 'wrong-id', 'young', 'failed-exit', 'missing-log', 'symlink-log', 'manifest-present', 'uncertain', 'missing-policy'])
def test_unsafe_or_unconfirmed_candidate_never_deletes(tmp_path, fault):
    candidate, inventory, calls, cli, options = host(tmp_path)
    run = options['runs'] / candidate['run_id']
    if fault == 'running': inventory[0]['phase'] = 'Ready'
    if fault == 'wrong-id': candidate['name'] = 'ip-' + 'f' * 16
    if fault == 'young': candidate['stopped_at'] = datetime.now(UTC).isoformat()
    if fault == 'failed-exit': (run / 'exit.json').write_text('{"exit_code":1}')
    if fault == 'missing-log': (run / 'events.jsonl').unlink()
    if fault == 'symlink-log':
        (run / 'error.log').unlink()
        (run / 'error.log').symlink_to(run / 'events.jsonl')
    if fault == 'manifest-present': (run / 'manifest.json').write_text('do not archive credentials')
    if fault == 'uncertain': candidate['evidence']['state'] = 'uncertain'
    if fault == 'missing-policy': options['policy'].unlink()
    retention_sweep([candidate], cli, apply=True, **options)
    assert len(inventory) == 2
    assert not any(call[:2] == ('sandbox', 'delete') for call in calls)


def test_uncertain_delete_is_not_repeated(tmp_path):
    candidate, inventory, calls, cli, options = host(tmp_path)
    attempts = []
    def disconnected(*args):
        if args[:2] == ('sandbox', 'delete'):
            attempts.append(args)
            raise RuntimeError('connection lost')
        return cli(*args)
    with pytest.raises(RuntimeError): retention_sweep([candidate], disconnected, apply=True, **options)
    result = retention_sweep([candidate], disconnected, apply=True, **options)
    assert len(attempts) == 1 and result['deleted'] == []
    assert 'no replay' in result['retained'][0]['reason']


def test_archive_failure_blocks_deletion(tmp_path):
    candidate, inventory, calls, cli, options = host(tmp_path)
    archive = options['archives'] / candidate['run_id']
    archive.mkdir(mode=0o700)
    (archive / 'events.jsonl').write_text('tampered archive')
    result = retention_sweep([candidate], cli, apply=True, **options)
    assert not result['deleted'] and len(inventory) == 2


def test_host_stop_age_is_independently_checked(tmp_path):
    candidate, inventory, calls, cli, options = host(tmp_path)
    stopped = options['sandboxes'] / str(UUID(candidate['sandbox_id'])) / 'stopped'
    os.utime(stopped, None)
    result = retention_sweep([candidate], cli, apply=True, **options)
    assert not result['deleted'] and len(inventory) == 2
    assert 'less than 24' in result['retained'][0]['reason']


def test_sandbox_activated_during_archive_is_retained(tmp_path):
    candidate, inventory, calls, cli, options = host(tmp_path)
    def changed(*args):
        result = cli(*args)
        if '--policy-only' in args: inventory[0]['phase'] = 'Ready'
        return result
    result = retention_sweep([candidate], changed, apply=True, **options)
    assert not result['deleted'] and len(inventory) == 2
    assert not any(call[:2] == ('sandbox', 'delete') for call in calls)


def test_cleanup_cannot_enter_while_lifecycle_lock_is_held(tmp_path):
    import fcntl
    candidate, inventory, calls, cli, options = host(tmp_path)
    with open(os.open(options['lock'], os.O_CREAT | os.O_RDWR, 0o600), 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = retention_sweep([candidate], cli, apply=True, **options)
    assert result['status'] == 'busy' and calls == []


def test_ambiguous_inventory_after_delete_stops_sweep(tmp_path):
    candidate, inventory, calls, cli, options = host(tmp_path)
    def changed(*args):
        result = cli(*args)
        if args[:2] == ('sandbox', 'delete'): inventory.clear()
        return result
    with pytest.raises(RuntimeError, match='inventory differs'):
        retention_sweep([candidate], changed, apply=True, **options)
    archive = options['archives'] / candidate['run_id']
    assert (archive / 'delete-attempt.json').exists()
    assert not (archive / 'deleted.json').exists()