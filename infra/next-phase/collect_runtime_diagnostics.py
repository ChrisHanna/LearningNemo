#!/usr/bin/env python3
"""Collect current control-plane state and prior failure evidence without recovery."""

import argparse
import ast
import base64
from datetime import UTC, datetime
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess

from diagnostic_attempt import DiagnosticAttempt, sanitize


STATE = Path.home() / '.local/state/learningnemo'
GROUP = 'rg-learningnemo-saw-dev'
VM = 'vm-learningnemo-saw-dev'
MAX_ARCHIVE = 262144
MAX_TEXT = 1048576


def historical_registry_diagnosis(parameters, response):
    allowed = parameters.get('parameters', {}).get('registryAddresses', {}).get('value', [])
    messages = '\n'.join(item.get('message', '') for item in response.get('value', []))
    observed = []
    for match in re.finditer(r'CURRENT_GHCR (\[[^\n]+?\])', messages):
        values = ast.literal_eval(match.group(1))
        if isinstance(values, list) and all(isinstance(value, str) for value in values):
            observed.extend(values)
    unmatched = [address for address in observed if address + '/32' not in allowed]
    return {'allowedRegistryAddresses': allowed, 'observedGhcrAddresses': observed,
        'outsideAllowlist': unmatched, 'registryControlTimedOut': 'REGISTRY_CONTROL 000' in messages,
        'conclusion': 'recorded DNS/firewall mismatch; capture same-run effective rules and curl metrics to confirm enforcement'
            if unmatched else 'insufficient recorded DNS evidence to identify a mismatch',
        'source': 'historical files, not current network telemetry'}


def summarize_cloud_logs(text):
    records = []
    for line in text.splitlines():
        try:
            wrapped = json.loads(line)
            message = wrapped.get('Log', '')
            timestamp = wrapped.get('TimeStamp')
        except ValueError:
            message, timestamp = line, None
        if not isinstance(message, str):
            continue
        for event in ('authorization_decision', 'semantic_guardrail_decision'):
            marker = event + ' {'
            if marker in message:
                try:
                    value, _ = json.JSONDecoder().raw_decode('{' + message.split(marker, 1)[1])
                    safe = {'event': event, 'time': timestamp}
                    for key in ('request_id', 'outcome', 'reason', 'tool_name', 'resource', 'decision'):
                        candidate = value.get(key)
                        if isinstance(candidate, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', candidate):
                            safe[key] = candidate
                    for key in ('missing_scopes', 'missing_roles', 'required_scopes', 'required_roles'):
                        candidates = value.get(key)
                        if isinstance(candidates, list) and len(candidates) <= 20 and all(isinstance(item,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',item) for item in candidates):
                            safe[key] = candidates
                    records.append(safe)
                except ValueError:
                    pass
        match = re.search(r'\b(agent_request_failed|walkthrough_agent_request_failed) status=(\d{3}) category=([a-z_]+) request_id=([a-f0-9]{32})', message)
        if match:
            failure = {'event': match[1], 'status': int(match[2]), 'category': match[3], 'request_id': match[4], 'time': timestamp}
            if ' validation_fields=' in message:
                try:
                    fields, _ = json.JSONDecoder().raw_decode(message.split(' validation_fields=', 1)[1])
                    allowed = {'body', 'query', 'path', 'header', 'messages', 'model', 'role', 'content', 'type', 'text', 'input_message', 'stream', '<field>'}
                    types = {'missing', 'string_type', 'list_type', 'dict_type', 'model_type', 'literal_error', 'extra_forbidden', 'json_invalid', 'value_error', 'validation_error'}
                    if isinstance(fields, list) and len(fields) <= 10:
                        failure['validationFields'] = [field for field in fields if isinstance(field, dict)
                            and set(field) == {'location', 'type'} and isinstance(field['type'], str) and field['type'] in types
                            and isinstance(field['location'], list) and len(field['location']) <= 8
                            and all(type(part) is int and 0 <= part < 10000 or isinstance(part, str) and part in allowed for part in field['location'])]
                except ValueError:
                    pass
            records.append(failure)
        match = re.search(r'"(GET|POST|DELETE) (/[^\s?]*)[^\s]* HTTP/[\d.]+" ([45]\d\d)', message)
        if match:
            records.append({'event': 'http_failure', 'method': match[1], 'path': match[2] if match[2] in ('/v1/chat/completions', '/api/chat', '/health', '/readyz') else '<other-route>', 'status': int(match[3]), 'time': timestamp})
        match = re.search(r'\b([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):', message)
        if match:
            records.append({'event': 'exception_type', 'type': match[1], 'time': timestamp})
        match = re.search(r'File "([^"\n]+\.py)", line (\d+), in ([A-Za-z0-9_<>]+)', message)
        if match:
            records.append({'event': 'traceback_frame', 'file': Path(match[1]).name, 'line': int(match[2]), 'function': match[3], 'time': timestamp})
    return {'records': records, 'inputLines': len(text.splitlines()),
            'detail': 'bounded available log tail; no matching events does not prove no failures; request/response text omitted'}


def archive_manifest(response, run_id):
    lines = [line for item in response.get('value', []) for line in item.get('message', '').splitlines() if line.startswith('SANDBOX_LOG_MANIFEST ')]
    if len(lines) != 1:
        raise ValueError('one complete guest archive manifest required')
    manifest = json.loads(lines[0].split(' ', 1)[1])
    if (manifest.get('runId') != run_id or type(manifest.get('bytes')) is not int
            or not 0 < manifest['bytes'] <= MAX_ARCHIVE
            or re.fullmatch(r'[a-f0-9]{64}', str(manifest.get('sha256'))) is None):
        raise ValueError('guest archive manifest binding or size differs')
    return manifest


def decode_archive(archive, manifest):
    if len(archive) != manifest['bytes'] or hashlib.sha256(archive).hexdigest() != manifest['sha256']:
        raise ValueError('guest archive integrity mismatch')
    with gzip.GzipFile(fileobj=io.BytesIO(archive)) as stream:
        content = stream.read(MAX_TEXT + 1)
    if len(content) > MAX_TEXT:
        raise ValueError('guest archive expanded beyond the limit')
    document = json.loads(content)
    if document.get('runId') != manifest['runId'] or not isinstance(document.get('files'), dict):
        raise ValueError('guest archive belongs to another attempt')
    return sanitize(document)


def collect_guest(attempt, run_id):
    if re.fullmatch(r'[a-f0-9]{32}', run_id) is None:
        raise ValueError('guest attempt must be a 32-character hexadecimal ID')
    archive_path = f'/var/lib/learningnemo-saw/attempts/{run_id}/logs.json.gz'
    script = f"""#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
from pathlib import Path
import hashlib,json
content=Path('{archive_path}').read_bytes()
print('SANDBOX_LOG_MANIFEST '+json.dumps({{'runId':'{run_id}','bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}}))
PY
"""
    prefix = ['vm','run-command','invoke','-g',GROUP,'-n',VM,'--command-id','RunShellScript','--scripts']
    manifest = archive_manifest(attempt.command('guest-log-manifest', [*prefix, script], timeout=120), run_id)
    archive = bytearray()
    for index, offset in enumerate(range(0, manifest['bytes'], 2048)):
        count = min(2048, manifest['bytes'] - offset)
        script = f"""#!/usr/bin/env bash
set -euo pipefail
printf 'SANDBOX_LOG_CHUNK {run_id} {index} '
dd if='{archive_path}' bs=1 skip={offset} count={count} status=none | base64 -w0
printf '\\n'
"""
        response = attempt.command('guest-log-chunk', [*prefix, script], timeout=120)
        marker = f'SANDBOX_LOG_CHUNK {run_id} {index} '
        lines = [line for item in response.get('value', []) for line in item.get('message', '').splitlines() if line.startswith(marker)]
        if len(lines) != 1:
            raise ValueError('missing or ambiguous guest log chunk')
        chunk = base64.b64decode(lines[0][len(marker):], validate=True)
        if len(chunk) != count:
            raise ValueError('guest log chunk length mismatch')
        archive.extend(chunk)
    document = decode_archive(bytes(archive), manifest)
    attempt.record('guest-logs-verified', document)
    return {'runId': run_id, 'files': sorted(document['files']), 'integrityVerified': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', help='Read prior evidence only; make no Azure calls')
    parser.add_argument('--cloud-agent-logs', action='store_true')
    parser.add_argument('--guest-attempt')
    args = parser.parse_args()
    attempt = DiagnosticAttempt(STATE / 'diagnostics', 'runtime-diagnosis')
    print(f'DIAGNOSTICS {attempt.run_id} / {attempt.directory}', flush=True)
    report = {'checkedAt': datetime.now(UTC).isoformat(), 'currentState': None, 'historical': {}, 'collectionIssues': []}
    saved = {}
    for name in ('workspace-registry-bootstrap.parameters.json', 'workspace-resume-sandboxes.result.json',
                 'workspace-runtime-egress.verified.json', 'workspace-runtime-egress.rollback-verified.json',
                 'workspace-renewal.verified.json', 'human-services.verified.json'):
        path = STATE / name
        if path.exists():
            try:
                saved[name] = json.loads(path.read_text())
                attempt.record('prior-evidence', {'file': name, 'modifiedAt': datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(), 'content': saved[name]})
            except (OSError, ValueError):
                report['collectionIssues'].append('unreadable saved file: ' + name)
    report['historical']['registry'] = historical_registry_diagnosis(saved.get('workspace-registry-bootstrap.parameters.json', {}), saved.get('workspace-resume-sandboxes.result.json', {}))
    if not args.offline:
        account = attempt.command('account', ['account','show'])
        if account.get('id') != os.environ.get('AZURE_SUBSCRIPTION_ID'):
            raise ValueError('pin the intended subscription before live diagnostics')
        vm = attempt.command('vm-state', ['vm','get-instance-view','-g',GROUP,'-n',VM])
        owned = vm.get('tags', {}).get('owner') == 'learningnemo-portfolio' and vm.get('tags', {}).get('project') == 'learningnemo'
        if not owned:
            raise ValueError('workspace ownership does not match')
        states = [item['code'] for item in vm.get('instanceView', {}).get('statuses', [])]
        subnet = attempt.command('subnet', ['network','vnet','subnet','show','-g',GROUP,'--vnet-name','vnet-learningnemo-saw-dev','-n','snet-workspace'])
        rules = attempt.command('nsg-rules', ['network','nsg','rule','list','-g',GROUP,'--nsg-name','nsg-vnet-learningnemo-saw-dev-workspace'])
        expiry = vm['tags'].get('expiresAt')
        report['currentState'] = {'vmStates': states, 'vmExpiresAt': expiry,
            'vmLeaseExpired': datetime.fromisoformat(expiry.replace('Z', '+00:00')) <= datetime.now(UTC) if expiry else None,
            'natAttached': bool(subnet.get('natGateway')), 'bootstrapRulePresent': any(rule['name']=='allow-bootstrap-ghcr-https' for rule in rules)}
        report['services'] = []
        for group in ('rg-learningnemo-demo-dev', 'rg-learningnemo-human-dev'):
            metadata = attempt.command('cloud-revisions', ['containerapp','list','-g',group,'--query', '[].{name:name,revision:properties.latestRevisionName,readyRevision:properties.latestReadyRevisionName,state:properties.runningStatus,expiresAt:tags.expiresAt}'])
            for service in metadata:
                expiry = service.get('expiresAt')
                service['leaseTagExpired'] = datetime.fromisoformat(expiry.replace('Z', '+00:00')) <= datetime.now(UTC) if expiry else None
            report['services'].extend(metadata)
        if args.guest_attempt:
            if 'PowerState/running' not in states:
                report['guestLogs'] = {'status': 'not-collected', 'reason': 'VM is not running; no automatic start permitted'}
            else:
                try:
                    report['guestLogs'] = collect_guest(attempt, args.guest_attempt)
                except Exception as error:
                    report['collectionIssues'].append('guest log retrieval: ' + str(error))
        if args.cloud_agent_logs:
            for service in ('dashboard', 'agent'):
                try:
                    response = subprocess.run(['az','containerapp','logs','show','-g','rg-learningnemo-demo-dev','-n',f'ca-learningnemo-{service}-dev','--type','console','--tail','300','--format','json'], capture_output=True, text=True, timeout=90, check=False)
                    if response.returncode:
                        raise RuntimeError('cloud log query failed')
                    summary = summarize_cloud_logs(response.stdout)
                    attempt.record('cloud-' + service + '-errors', summary)
                    report.setdefault('cloudLogCounts', {})[service] = len(summary['records'])
                except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
                    report['collectionIssues'].append(f'{service} log collection: {type(error).__name__}')
    attempt.record('diagnosis', report)
    attempt.event('collection', 'partial' if report['collectionIssues'] else 'complete')
    print(json.dumps(sanitize(report), indent=2), flush=True)
    print('INFO diagnostics only: no start, restart, renewal, deployment, or incident mutation was performed', flush=True)


if __name__ == '__main__':
    main()