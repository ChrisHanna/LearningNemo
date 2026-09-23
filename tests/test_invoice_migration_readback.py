from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

DIRECTORY = Path(__file__).parents[1] / 'infra/next-phase'
sys.path.insert(0, str(DIRECTORY))
spec = importlib.util.spec_from_file_location('invoice_release_readback', DIRECTORY / 'deploy_invoice_services.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def values():
    parameters = {key: {'value': value} for key, value in {'image': 'owned@sha256:fixture', 'sqlAdminClientId': 'admin', 'sqlServer': 'private-sql', 'sqlDatabase': 'learningnemo', 'expiresAt': '2026-09-19T02:00:00+00:00'}.items()}
    live = {'principals': {'operator': {'clientId': 'operator'}}}
    environment = {'AZURE_CLIENT_ID': 'admin', 'LEARNINGNEMO_SQL_SERVER': 'private-sql', 'LEARNINGNEMO_SQL_DATABASE': 'learningnemo', 'LEARNINGNEMO_MIGRATION_EXPIRES_AT': parameters['expiresAt']['value'], 'LEARNINGNEMO_INVOICE_PRINCIPALS': json.dumps(live['principals'])}
    execution = {'name': 'readback', 'properties': {'status': 'Succeeded', 'startTime': '2026-09-19T01:00:00+00:00', 'endTime': '2026-09-19T01:01:00+00:00',
        'template': {'containers': [{'command': ['python', '/app/scripts/apply-invoice-migrations.py', '--verify-only'], 'image': parameters['image']['value'], 'env': [{'name': key, 'value': value} for key, value in environment.items()]}]}}}
    return execution, live, parameters


def test_successful_exact_readback_program_provides_explicit_non_log_evidence():
    execution, live, parameters = values()
    receipt = release.readback_execution_receipt(execution, live, parameters, {'migration': 'hash'})
    assert receipt['legacyDeadlineCheckPassed'] is True
    assert receipt['legacyApprovalDeadlineCheckPassed'] is True
    assert receipt['reviewWindowMinutes'] == 180 and receipt['approvalWindowMinutes'] == 30
    assert receipt['evidence']['source'] == 'pinned-verification-job-exit'
    assert 'existingReviewDeadlinesPreserved' not in receipt


@pytest.mark.parametrize('fault', ['failed', 'apply-mode', 'wrong-image', 'wrong-principals', 'late', 'sidecar'])
def test_wrong_program_or_unconfirmed_execution_cannot_provide_proof(fault):
    execution, live, parameters = values()
    properties = execution['properties']
    container = properties['template']['containers'][0]
    if fault == 'failed': properties['status'] = 'Failed'
    if fault == 'apply-mode': container['command'].pop()
    if fault == 'wrong-image': container['image'] = 'another-image'
    if fault == 'wrong-principals': live['principals'] = {}
    if fault == 'late': properties['endTime'] = '2026-09-19T03:00:00+00:00'
    if fault == 'sidecar': properties['template']['containers'].append(deepcopy(container))
    with pytest.raises(ValueError): release.readback_execution_receipt(execution, live, parameters, {})