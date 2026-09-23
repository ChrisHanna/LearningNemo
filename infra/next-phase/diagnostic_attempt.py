"""Private, correlated evidence that survives a failed command and its cleanup."""

from datetime import UTC, datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from uuid import uuid4


SENSITIVE_KEY = re.compile(r'(?i)(authorization|cookie|password|secret|credential|private.?key|token|api.?key|client.?assertion|user.?code|device.?code)')
PEM = re.compile(r'-----BEGIN [^-]*(?:PRIVATE KEY|CERTIFICATE)-----.*?(?:-----END [^-]+-----|$)', re.S)
BEARER = re.compile(r'(?i)\bBearer\s+[^\s,\"\x27<>]+')
JWT = re.compile(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?')
SECRET_VALUE = re.compile(r'''(?ix)(["']?(?:authorization|cookie|password|secret|credential|private[_-]?key|access[_-]?token|refresh[_-]?token|token|api[_-]?key|client[_-]?secret)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,}]+)''')
URL_QUERY = re.compile(r'(https?://[^\s?#]+)[?#][^\s]*', re.I)
URL_USER = re.compile(r'(https?://)[^/@\s]+:[^/@\s]+@', re.I)


def sanitize(value):
    if isinstance(value, dict):
        return {str(key): '<redacted>' if SENSITIVE_KEY.search(str(key)) else sanitize(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [sanitize(child) for child in value]
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    if isinstance(value, str):
        value = PEM.sub('<private-material-redacted>', value)
        value = URL_USER.sub(r'\1<redacted>@', value)
        value = URL_QUERY.sub(r'\1?<redacted>', value)
        value = BEARER.sub('Bearer <redacted>', value)
        value = JWT.sub('<jwt-redacted>', value)
        return SECRET_VALUE.sub(r'\1<redacted>', value)
    return value


class DiagnosticCommandError(RuntimeError):
    pass


class DiagnosticAttempt:
    def __init__(self, root: Path, purpose: str, *, run_id: str | None = None):
        self.run_id = run_id or uuid4().hex
        if re.fullmatch(r'[0-9a-f]{32}', self.run_id) is None or re.fullmatch(r'[a-z0-9-]{1,60}', purpose) is None:
            raise ValueError('invalid diagnostic context')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = root / self.run_id
        self.directory.mkdir(mode=0o700)
        self.sequence = 0
        self.event('attempt', 'started', purpose=purpose)

    def _write(self, name: str, value):
        path = self.directory / name
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as stream:
            json.dump(sanitize(value), stream, indent=2, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        return path

    def event(self, stage: str, outcome: str, **details):
        record = sanitize({'runId': self.run_id, 'time': datetime.now(UTC).isoformat(), 'stage': stage, 'outcome': outcome, **details})
        path = self.directory / 'events.jsonl'
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, separators=(',', ':')) + '\n')
            stream.flush()
            os.fsync(stream.fileno())

    def record(self, stage: str, document):
        if re.fullmatch(r'[a-z0-9-]{1,80}', stage) is None:
            raise ValueError('invalid evidence name')
        self.sequence += 1
        return self._write(f'{self.sequence:03d}-{stage}.json', {'runId': self.run_id, 'capturedAt': datetime.now(UTC).isoformat(), 'data': document})

    def command(self, stage: str, arguments: list[str], *, timeout: int = 600, runner=subprocess.run):
        metadata = {}
        if '--scripts' in arguments:
            script = arguments[arguments.index('--scripts') + 1]
            metadata['scriptSha256'] = hashlib.sha256(script.encode()).hexdigest()
        self.event(stage, 'started', **metadata)
        started = time.monotonic()
        try:
            result = runner(['az', *arguments, '--output', 'json', '--only-show-errors'], capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            evidence = self.record(stage, {'category': 'timeout', 'stdout': error.stdout, 'stderr': error.stderr})
            self.event(stage, 'failed', category='timeout', evidence=evidence.name, durationMs=round((time.monotonic()-started)*1000))
            raise DiagnosticCommandError(f'{stage} timed out; inspect attempt {self.run_id}; completion is unknown') from None
        except OSError as error:
            self.record(stage, {'category': 'process-unavailable', 'errorType': type(error).__name__})
            self.event(stage, 'failed', category='process-unavailable')
            raise DiagnosticCommandError(f'{stage} could not start; inspect attempt {self.run_id}') from None
        try:
            document = json.loads(result.stdout.lstrip('\ufeff')) if result.stdout.strip() else None
            parsed = True
        except ValueError:
            document, parsed = None, False
        evidence = self.record(stage, {'exitCode': result.returncode, 'response': document if parsed else result.stdout,
                                       'stderr': result.stderr})
        if result.returncode or not parsed:
            category = 'azure-cli-error' if result.returncode else 'invalid-json'
            self.event(stage, 'failed', category=category, exitCode=result.returncode, evidence=evidence.name)
            raise DiagnosticCommandError(f'{stage} failed ({category}); inspect attempt {self.run_id}') from None
        self.event(stage, 'returned', evidence=evidence.name, durationMs=round((time.monotonic()-started)*1000))
        return document


def bundle_guest_logs(directory: Path):
    if re.fullmatch(r'[a-f0-9]{32}', directory.name) is None:
        raise ValueError('invalid guest log directory')
    files = {}
    limit = 49152
    candidates = sorted(directory.glob('*.raw'), key=lambda path: (0 if path.name in {'events.raw', 'rebootstrap.raw'} or path.name.startswith(('resolver-', 'create-', 'registry-')) else 1, path.name))[:12]
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('rb') as stream:
            size = path.stat().st_size
            stream.seek(max(0, size - limit))
            text = stream.read(limit).decode('utf-8', errors='replace')
        files[path.name] = {'truncated': size > limit, 'originalBytes': size, 'tail': sanitize(text)}
    payload = json.dumps({'runId': directory.name, 'capturedAt': datetime.now(UTC).isoformat(), 'files': files}, ensure_ascii=True).encode()
    archive = gzip.compress(payload, mtime=0)
    with open(os.open(directory / 'logs.json.gz', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(archive)
    for path in candidates:
        path.unlink()
    manifest = {'runId': directory.name, 'bytes': len(archive), 'sha256': hashlib.sha256(archive).hexdigest()}
    print('SANDBOX_LOG_MANIFEST ' + json.dumps(manifest, separators=(',', ':')))
    return manifest


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Sanitize and bundle bounded guest diagnostics')
    parser.add_argument('--bundle', type=Path, required=True)
    bundle_guest_logs(parser.parse_args().bundle)