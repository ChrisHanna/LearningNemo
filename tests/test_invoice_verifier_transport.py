import importlib
from pathlib import Path

import pytest


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'infra/next-phase'))
    return importlib.import_module('verify_invoice_services')


@pytest.mark.parametrize('text,expected', [
    ('Network is unreachable', True), ('Failed to establish a new connection', True),
    ('429 Network is unreachable', False), ('Retry-After Network is unreachable', False),
    ('Successfully connected Network is unreachable', False), ('PASS invoice_sql_operator Network is unreachable', False),
    ('SQL connection failed', False),
])
def test_verification_retry_is_preconnection_only(verifier, text, expected):
    assert verifier.retryable_network_failure(text) is expected


@pytest.mark.parametrize('read_only,attempts', [(False, 1), (True, 3)])
def test_remote_retry_is_explicit_and_bounded(verifier, monkeypatch, read_only, attempts):
    calls=[]
    monkeypatch.setattr(verifier.os, 'read', lambda *_: b'Network is unreachable')
    def spawn(command, master_read):
        calls.append(command)
        master_read(1)
        return 1
    monkeypatch.setattr(verifier.pty, 'spawn', spawn)
    with pytest.raises(RuntimeError):
        verifier.remote('operator', 'print(1)', 'PASS', revision='revision', replica='replica', read_only=read_only)
    assert len(calls)==attempts
    assert calls[0][calls[0].index('--replica')+1]=='replica'


@pytest.mark.parametrize('stderr,attempts', [(b'', 3), (b'429 Retry-After: 600', 1)])
def test_metadata_timeout_is_bounded_and_never_retries_throttling(verifier, monkeypatch, stderr, attempts):
    calls=[]
    def run(command, **kwargs):
        calls.append(command)
        raise verifier.subprocess.TimeoutExpired(command, 90, stderr=stderr)
    monkeypatch.setattr(verifier.subprocess, 'run', run)
    with pytest.raises(verifier.subprocess.TimeoutExpired):
        verifier.read_az('containerapp', 'show')
    assert len(calls)==attempts