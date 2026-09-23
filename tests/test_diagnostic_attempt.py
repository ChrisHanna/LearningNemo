import importlib.util
import json
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('diagnostic_attempt', ROOT / 'infra/next-phase/diagnostic_attempt.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_each_attempt_retains_evidence_and_private_permissions(tmp_path):
    attempt = MODULE.DiagnosticAttempt(tmp_path, 'sandbox-resume')
    first = attempt.record('dns', {'ghcr.io': ['140.82.112.34']})
    second = attempt.record('dns', {'ghcr.io': ['140.82.114.33']})
    assert first != second
    assert '140.82.112.34' in first.read_text()
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert stat.S_IMODE(attempt.directory.stat().st_mode) == 0o700
    with pytest.raises(FileExistsError):
        MODULE.DiagnosticAttempt(tmp_path, 'sandbox-resume', run_id=attempt.run_id)


def test_redaction_preserves_diagnostic_ips_but_not_credentials():
    source = {'authorization': 'Bearer abc', 'private_key': 'key', 'deviceCode': 'CODE',
        'Log': 'address=140.82.112.34 Authorization: Bearer abc password="two words" eyJabc.def.signature https://host.test/route?sig=private#fragment',
        'trace': '-----BEGIN PRIVATE KEY-----\nsecret material\n-----END PRIVATE KEY-----\nTLS failed',
        'url': 'https://user:password@host.test/x?token=abc'}
    result = json.dumps(MODULE.sanitize(source))
    for secret in ('Bearer abc', 'two words', 'eyJabc', 'secret material', 'sig=private', 'user:password', 'token=abc', 'CODE'):
        assert secret not in result
    assert '140.82.112.34' in result and 'TLS failed' in result


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'json'])
def test_failure_is_saved_without_logging_command_or_masking_primary_error(tmp_path, failure):
    attempt = MODULE.DiagnosticAttempt(tmp_path, 'sandbox-resume')
    def runner(*args, **kwargs):
        if failure == 'timeout': raise subprocess.TimeoutExpired(args[0], 20, output=b'Bearer private-token', stderr=b'connection timeout')
        return SimpleNamespace(returncode=1 if failure == 'exit' else 0, stdout='not JSON' if failure == 'json' else '{}', stderr='secret=private-token')
    with pytest.raises(MODULE.DiagnosticCommandError) as caught:
        attempt.command('sandbox-provision', ['vm','run-command','invoke','--scripts','sensitive script'], runner=runner)
    attempt.event('cleanup', 'failed', category='separate-cleanup-failure')
    evidence = '\n'.join(path.read_text() for path in attempt.directory.iterdir())
    assert 'sensitive script' not in evidence and 'private-token' not in evidence
    assert 'sandbox-provision' in evidence and 'cleanup' in evidence
    assert 'sensitive script' not in str(caught.value)


def test_successful_cli_transport_is_not_labelled_guest_success(tmp_path):
    attempt = MODULE.DiagnosticAttempt(tmp_path, 'sandbox-resume')
    result = attempt.command('guest', ['vm','run-command'], runner=lambda *args, **kwargs: SimpleNamespace(returncode=0,
        stdout=json.dumps({'value':[{'message':'guest failed'}]}), stderr=''))
    assert result['value'][0]['message'] == 'guest failed'
    events = [json.loads(line) for line in (attempt.directory/'events.jsonl').read_text().splitlines()]
    assert events[-1]['outcome'] == 'returned'


def test_guest_archive_is_bounded_sanitized_and_digest_bound(tmp_path):
    import gzip
    import hashlib
    directory = tmp_path / ('a'*32)
    directory.mkdir()
    (directory/'gateway.raw').write_text('padding'*10000 + '\nAuthorization: Bearer secret-token\nIP 140.82.112.34\n')
    (directory/'events.raw').write_text('runId='+'a'*32)
    manifest = MODULE.bundle_guest_logs(directory)
    archive = (directory/'logs.json.gz').read_bytes()
    assert manifest['sha256'] == hashlib.sha256(archive).hexdigest()
    document = json.loads(gzip.decompress(archive))
    assert document['files']['gateway.raw']['truncated'] is True
    assert 'secret-token' not in json.dumps(document)
    assert '140.82.112.34' in json.dumps(document)
    assert not list(directory.glob('*.raw'))


def test_guest_prerequisite_failure_produces_retrievable_sanitized_archive(tmp_path):
    import base64
    import gzip
    run = 'd'*32
    script = (ROOT/'scripts/resume-saw-sandboxes.sh').read_text()
    script = script.replace('__ACTION__', 'create')
    script = script.replace('__BOOTSTRAP_HELPER_B64__', base64.b64encode((ROOT/'scripts/repair-sandbox-bootstrap.py').read_bytes()).decode())
    script = script.replace('__RESOLVER_HELPER_B64__', base64.b64encode((ROOT/'scripts/repair-sandbox-resolver.sh').read_bytes()).decode())
    script = script.replace('__PIN_ID__', run).replace('__LOG_HELPER_B64__', base64.b64encode((ROOT/'infra/next-phase/diagnostic_attempt.py').read_bytes()).decode())
    script = script.replace('/var/lib/learningnemo-saw', str(tmp_path))
    script = script.replace('admin_uid=$(id -u sawadmin)', 'admin_uid=$(false)')
    result = subprocess.run(['bash'], input=script, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'SANDBOX_LOG_MANIFEST' in result.stdout
    assert 'SANDBOX_LOG_CAPTURE_FAILED' not in result.stdout
    document = json.loads(gzip.decompress((tmp_path/'attempts'/run/'logs.json.gz').read_bytes()))
    events = [json.loads(line) for line in document['files']['events.raw']['tail'].splitlines()]
    assert events[-1]['stage'] == 'prerequisites' and events[-1]['outcome'] == 'failed'
    assert events[-1]['exitCode'] != 0
    assert not (tmp_path/f'hosts-before-{run}').exists()