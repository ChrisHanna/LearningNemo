"""Standalone host cleanup; evidence survives outside disposable MicroVM storage."""

from datetime import UTC, datetime, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from uuid import UUID


RETENTION_POLICY = {'version': 3, 'enabled': True, 'successful_stopped_ttl_hours': 24,
                    'stopped_sandbox_ttl_hours': 24, 'cleanup_at_count': 11, 'target_count': 10}


def sandbox_quiescent(record, sandboxes=Path('/home/sawadmin/.local/state/openshell/vm/sandboxes'), processes=Path('/proc')):
    if record.get('phase') == 'Stopped':
        return True
    if record.get('phase') != 'Error':
        return False
    try:
        identifier = str(UUID(record['id']))
        name = record['name']
    except (KeyError, ValueError, TypeError):
        return False
    directory = sandboxes / identifier
    if directory.exists() or directory.is_symlink():
        return False
    needles = (identifier.encode(), name.encode())
    for command in processes.glob('[0-9]*/cmdline'):
        try:
            with command.open('rb') as source:
                content = source.read(65536)
        except (FileNotFoundError, PermissionError, OSError):
            continue
        if any(needle in content for needle in needles):
            return False
    return True


def safe_path(path):
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('symlink in retention path')


def private_directory(path, owner):
    safe_path(path)
    path.mkdir(mode=0o700, exist_ok=True)
    metadata = path.stat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != owner or metadata.st_mode & 0o077:
        raise ValueError('retention directory is not private')


def read_bounded(path, limit=8 * 1024 * 1024):
    safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            raise ValueError('archive source invalid or oversized')
        data = source.read(limit + 1)
        if len(data) > limit:
            raise ValueError('archive source exceeds limit')
        return data


def write_once(path, data):
    safe_path(path)
    if path.exists():
        if read_bounded(path) != data:
            raise ValueError('existing archive differs')
        return
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600), 'wb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def shared_delete_attempt(archives, sandbox_id, owner):
    private_directory(archives, owner)
    attempts = archives / 'delete-attempts'
    private_directory(attempts, owner)
    return attempts / (sandbox_id + '.json')


def validate_host_binding(candidate, record, now, minimum_age_hours=24):
    run_id, sandbox_id, kind = candidate['run_id'], candidate['sandbox_id'], candidate['kind']
    if kind not in ('planning', 'execution') or any(not re.fullmatch('[a-f0-9]{32}', value) for value in (run_id, sandbox_id)):
        raise ValueError('invalid cleanup identifier')
    expected_name = ('ip-' if kind == 'planning' else 'ix-') + run_id[:16]
    if candidate['name'] != expected_name or record['name'] != expected_name or UUID(record['id']).hex != sandbox_id or record['phase'] != 'Stopped':
        raise ValueError('sandbox identity or stopped state differs')
    stopped = datetime.fromisoformat(candidate['stopped_at'].replace('Z', '+00:00'))
    evidence = candidate['evidence']
    if stopped.tzinfo is None or stopped > now - timedelta(hours=minimum_age_hours) or evidence['state'] != 'finished':
        raise ValueError('successful stopped retention period not met')
    if evidence['run_id'] != run_id or evidence['sandbox_id'] != sandbox_id or evidence['kind'] != kind:
        raise ValueError('SQL cleanup binding differs')


def archive_run(candidate, record, cli, runs, policies, sandboxes, archives, owner, now, write=True, minimum_age_hours=24):
    run_id = candidate['run_id']
    directory = runs / run_id
    safe_path(directory)
    if directory.stat().st_uid != owner or directory.stat().st_mode & 0o077:
        raise ValueError('run directory is not private')
    if (directory / 'key.pem').exists() or (directory / 'manifest.json').exists():
        raise ValueError('run still has delivery credentials')
    files = {name: read_bounded(directory / name) for name in ('events.jsonl', 'error.log', 'exit.json', 'launch-receipt.json')}
    if candidate['evidence']['result'].get('sandbox_test'):
        for name in ('probe-started','probe-receipt.json'):
            files[name] = read_bounded(directory / name)
    if json.loads(files['exit.json']) != {'exit_code': 0}:
        raise ValueError('agent exit was not successful')
    if json.loads(files['launch-receipt.json']).get('sandbox_id') != candidate['sandbox_id']:
        raise ValueError('agent launch binding differs')
    events = [json.loads(line) for line in files['events.jsonl'].splitlines() if line.startswith(b'{')]
    if not events or events[-1].get('event_type') != 'agent-finished':
        raise ValueError('agent completion transcript missing')
    if any(event.get('source') != 'agent-runtime' or event.get('run_id') != run_id or event.get('sandbox_id') != candidate['sandbox_id'] for event in events):
        raise ValueError('agent transcript binding differs')
    files['configured-policy.yaml'] = read_bounded(policies / (run_id + '.yaml'))
    files['observed-policy.yaml'] = cli('sandbox', 'get', candidate['name'], '--policy-only').encode()
    location = sandboxes / str(UUID(candidate['sandbox_id']))
    safe_path(location / 'stopped')
    if datetime.fromtimestamp((location / 'stopped').stat().st_mtime, UTC) > now - timedelta(hours=minimum_age_hours):
        raise ValueError(f'host stop is less than {minimum_age_hours} hours old')
    for name in ('rootfs-console.log', 'gvproxy.log', 'image-reference', 'image-identity', 'sandbox.pb', 'stopped'):
        files[name] = read_bounded(location / name)
    files['sql-evidence.json'] = encoded(candidate)
    files['sandbox.json'] = encoded(record)
    if not write:
        return None
    private_directory(archives, owner)
    archive = archives / run_id
    private_directory(archive, owner)
    for name, data in files.items():
        write_once(archive / name, data)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    write_once(archive / 'manifest.json', encoded({'version': 1, 'run_id': run_id, 'sandbox_id': candidate['sandbox_id'], 'sha256': hashes}))
    for name, digest in json.loads(read_bounded(archive / 'manifest.json'))['sha256'].items():
        if hashlib.sha256(read_bounded(archive / name)).hexdigest() != digest:
            raise ValueError('archive verification failed')
    return archive


def archive_stopped_sandbox(record, cli, sandboxes, archives, owner, now, *, write=True, minimum_age_hours=24):
    identifier = UUID(record['id']).hex
    if record.get('phase') != 'Stopped' or not re.fullmatch(r'i[px]-[a-f0-9]{16}', record.get('name', '')):
        raise ValueError('only stopped invoice sandboxes are eligible')
    location = sandboxes / str(UUID(record['id']))
    safe_path(location / 'stopped')
    stopped_at = datetime.fromtimestamp((location / 'stopped').stat().st_mtime, UTC)
    if stopped_at > now - timedelta(hours=minimum_age_hours):
        raise ValueError('host stop retention period not met')
    files = {
        'sandbox.json': encoded(record),
        'observed-policy.yaml': cli('sandbox', 'get', record['name'], '--policy-only').encode(),
    }
    for name in ('rootfs-console.log', 'gvproxy.log', 'image-reference', 'image-identity', 'sandbox.pb', 'stopped'):
        files[name] = read_bounded(location / name)
    files['retention-class.json'] = encoded({
        'version': 1, 'class': 'stopped-host-evidence', 'sandbox_id': identifier,
        'name': record['name'], 'stopped_at': stopped_at.isoformat(),
        'reason': 'No current SQL-bound successful-run candidate was available.',
    })
    if not write:
        return stopped_at
    private_directory(archives, owner)
    archive = archives / ('stopped-' + identifier)
    private_directory(archive, owner)
    for name, data in files.items():
        write_once(archive / name, data)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    write_once(archive / 'manifest.json', encoded({
        'version': 1, 'sandbox_id': identifier, 'name': record['name'], 'sha256': hashes,
    }))
    for name, digest in json.loads(read_bounded(archive / 'manifest.json'))['sha256'].items():
        if hashlib.sha256(read_bounded(archive / name)).hexdigest() != digest:
            raise ValueError('stopped sandbox archive verification failed')
    return stopped_at


def retention_sweep(candidates, cli, *, apply=False, now=None, pressure=False, manual_id=None,
                    runs=Path('/var/lib/learningnemo-invoice-cache/runs'),
                    policies=Path('/var/lib/learningnemo-invoice-cache/policies'),
                    sandboxes=Path('/home/sawadmin/.local/state/openshell/vm/sandboxes'),
                    archives=Path('/var/lib/learningnemo-invoice-retention'),
                    policy=Path('/etc/learningnemo/invoice-retention.json'),
                    lock=Path('/run/lock/learningnemo-invoice-lifecycle.lock'), owner=0):
    now = now or datetime.now(UTC)
    if manual_id is not None and (not apply or not re.fullmatch('[a-f0-9]{32}', manual_id)):
        raise ValueError('exact sandbox deletion identifier required')
    if apply:
        if not policy.exists():
            return {'status': 'disabled', 'deleted': [], 'retained': []}
        safe_path(policy)
        if policy.stat().st_uid != owner or policy.stat().st_mode & 0o022 or policy.parent.stat().st_uid != owner or policy.parent.stat().st_mode & 0o022:
            raise ValueError('retention policy is not owner-controlled')
        if json.loads(read_bounded(policy)) != RETENTION_POLICY:
            return {'status': 'disabled', 'deleted': [], 'retained': []}
    safe_path(lock)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'r+') as held:
        if os.fstat(held.fileno()).st_uid != owner or os.fstat(held.fileno()).st_mode & 0o077:
            raise ValueError('lifecycle lock ownership differs')
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy', 'deleted': [], 'retained': []}
        inventory = json.loads(cli('sandbox', 'list', '--output', 'json'))
        initial_count = len(inventory)
        active = any(not sandbox_quiescent(item, sandboxes) for item in inventory)
        before = {UUID(item['id']).hex: item['name'] for item in inventory}
        deleted, eligible, retained = [], [], []
        summary = {UUID(item['id']).hex: {'sandbox_id': UUID(item['id']).hex, 'name': item['name'], 'phase': item['phase'],
               'deletable': False, 'reason': 'Active sandbox present' if active else 'Quiescent error retained; not eligible for deletion' if item['phase'] == 'Error' else 'No verified completed run available for deletion'} for item in inventory}
        pressure_cleanup = pressure and len(inventory) >= RETENTION_POLICY['cleanup_at_count']
        seen = set()
        for candidate in sorted(candidates, key=lambda item: (item['stopped_at'], item['run_id'])):
            sandbox_id = candidate['sandbox_id']
            if sandbox_id not in before or sandbox_id in seen:
                continue
            seen.add(sandbox_id)
            summary[sandbox_id].update(run_id=candidate['run_id'], kind=candidate['kind'], stopped_at=candidate['stopped_at'])
            if active:
                continue
            try:
                record = json.loads(cli('sandbox', 'get', before[sandbox_id], '--output', 'json'))
                minimum_age = 0 if pressure or manual_id is not None else 24
                validate_host_binding(candidate, record, now, minimum_age)
                attempt = archives / candidate['run_id'] / 'delete-attempt.json'
                shared_attempt = archives / 'delete-attempts' / (sandbox_id + '.json')
                if attempt.exists() or shared_attempt.exists():
                    raise ValueError('prior deletion outcome needs inspection; no replay')
                archive_run(candidate, record, cli, runs, policies, sandboxes, archives, owner, now, write=False, minimum_age_hours=minimum_age)
                summary[sandbox_id].update(deletable=True, reason='Verified completed run; stopped and authority revoked')
                eligible.append(candidate['run_id'])
                expired = datetime.fromisoformat(candidate['stopped_at']) <= now - timedelta(hours=24)
                requested = sandbox_id == manual_id if manual_id is not None else expired or pressure_cleanup and initial_count - len(deleted) > RETENTION_POLICY['target_count']
                if not apply or not requested or len(deleted) >= (1 if manual_id is not None else 2):
                    continue
                deletion_age = 0 if manual_id is not None or pressure_cleanup else 24
                validate_host_binding(candidate, record, now, deletion_age)
                archive = archive_run(candidate, record, cli, runs, policies, sandboxes, archives, owner, now, minimum_age_hours=deletion_age)
                current = json.loads(cli('sandbox', 'list', '--output', 'json'))
                if any(not sandbox_quiescent(item, sandboxes) for item in current):
                    raise ValueError('active sandbox appeared before deletion')
                exact = next(item for item in current if UUID(item['id']).hex == sandbox_id)
                validate_host_binding(candidate, exact, now, deletion_age)
                shared_attempt = shared_delete_attempt(archives, sandbox_id, owner)
                write_once(shared_attempt, encoded({'run_id': candidate['run_id'], 'sandbox_id': sandbox_id,
                    'name': candidate['name'], 'requested_at': now.isoformat()}))
                write_once(attempt, encoded({'run_id': candidate['run_id'], 'sandbox_id': sandbox_id, 'name': candidate['name'], 'requested_at': now.isoformat()}))
                cli('sandbox', 'delete', candidate['name'])
                after = json.loads(cli('sandbox', 'list', '--output', 'json'))
                expected = {UUID(item['id']).hex: item['name'] for item in current if UUID(item['id']).hex != sandbox_id}
                if {UUID(item['id']).hex: item['name'] for item in after} != expected:
                    raise RuntimeError('post-delete inventory differs; no further deletion')
                write_once(archive / 'deleted.json', encoded({'run_id': candidate['run_id'], 'sandbox_id': sandbox_id,
                    'confirmed_at': datetime.now(UTC).isoformat(), 'remaining_count': len(after)}))
                deleted.append(candidate['run_id'])
                summary[sandbox_id].update(deletable=False, reason='Deletion confirmed; evidence archived', phase='Deleted')
            except (ValueError, KeyError, OSError, StopIteration) as error:
                retained.append({'run_id': candidate['run_id'], 'reason': type(error).__name__ + ': ' + str(error)[:160]})
                summary[sandbox_id].update(deletable=False, reason='Evidence or cleanup confirmation requires inspection')
        fallback = []
        for record in inventory:
            sandbox_id = UUID(record['id']).hex
            if sandbox_id in seen or active:
                continue
            try:
                stopped_at = archive_stopped_sandbox(record, cli, sandboxes, archives, owner, now, write=False,
                    minimum_age_hours=RETENTION_POLICY['stopped_sandbox_ttl_hours'])
                summary[sandbox_id].update(
                    deletable=True, stopped_at=stopped_at.isoformat(),
                    reason='Stopped for 24 hours; host evidence will be archived before deletion')
                fallback.append((stopped_at, sandbox_id, record))
            except (ValueError, KeyError, OSError) as error:
                retained.append({'sandbox_id': sandbox_id, 'reason': type(error).__name__ + ': ' + str(error)[:160]})
        for _stopped_at, sandbox_id, record in sorted(fallback):
            if manual_id is not None or not apply or initial_count - len(deleted) <= RETENTION_POLICY['target_count'] or len(deleted) >= 2:
                continue
            archive = archives / ('stopped-' + sandbox_id)
            attempt = archive / 'delete-attempt.json'
            shared_attempt = archives / 'delete-attempts' / (sandbox_id + '.json')
            if attempt.exists() or shared_attempt.exists():
                retained.append({'sandbox_id': sandbox_id, 'reason': 'prior deletion outcome needs inspection; no replay'})
                summary[sandbox_id].update(deletable=False, reason='Prior deletion outcome requires inspection')
                continue
            try:
                archive_stopped_sandbox(record, cli, sandboxes, archives, owner, now,
                    minimum_age_hours=RETENTION_POLICY['stopped_sandbox_ttl_hours'])
                current = json.loads(cli('sandbox', 'list', '--output', 'json'))
                if any(not sandbox_quiescent(item, sandboxes) for item in current):
                    raise ValueError('active sandbox appeared before stopped-sandbox deletion')
                exact = next(item for item in current if UUID(item['id']).hex == sandbox_id)
                archive_stopped_sandbox(exact, cli, sandboxes, archives, owner, now, write=False,
                    minimum_age_hours=RETENTION_POLICY['stopped_sandbox_ttl_hours'])
                shared_attempt = shared_delete_attempt(archives, sandbox_id, owner)
            except (ValueError, KeyError, OSError, StopIteration) as error:
                retained.append({'sandbox_id': sandbox_id, 'reason': type(error).__name__ + ': ' + str(error)[:160]})
                summary[sandbox_id].update(deletable=False, reason='Host evidence or cleanup confirmation requires inspection')
                continue
            write_once(shared_attempt, encoded({
                'sandbox_id': sandbox_id, 'name': record['name'], 'requested_at': now.isoformat(),
            }))
            write_once(attempt, encoded({
                'sandbox_id': sandbox_id, 'name': record['name'], 'requested_at': now.isoformat(),
            }))
            cli('sandbox', 'delete', record['name'])
            after = json.loads(cli('sandbox', 'list', '--output', 'json'))
            expected = {UUID(item['id']).hex: item['name'] for item in current if UUID(item['id']).hex != sandbox_id}
            if {UUID(item['id']).hex: item['name'] for item in after} != expected:
                raise RuntimeError('post-delete inventory differs; no further deletion')
            write_once(archive / 'deleted.json', encoded({
                'sandbox_id': sandbox_id, 'name': record['name'],
                'confirmed_at': datetime.now(UTC).isoformat(), 'remaining_count': len(after),
            }))
            deleted.append('stopped:' + sandbox_id)
            summary[sandbox_id].update(deletable=False, reason='Deletion confirmed; host evidence archived', phase='Deleted')
        remaining = json.loads(cli('sandbox', 'list', '--output', 'json'))
        disk = os.statvfs(runs)
        return {'status': 'active-sandbox' if active else 'applied' if apply else 'preview', 'eligible': eligible, 'deleted': deleted, 'retained': retained,
                'before_count': initial_count, 'remaining_count': len(remaining), 'free_slots': max(0, 24 - len(remaining)),
                'free_gib': round(disk.f_bavail * disk.f_frsize / 1024**3, 2), 'limit': 24,
                'cleanup_at_count': RETENTION_POLICY['cleanup_at_count'],
                'target_count': RETENTION_POLICY['target_count'], 'sandboxes': list(summary.values())}