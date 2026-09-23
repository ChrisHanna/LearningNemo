import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT/'infra/next-phase'))
SPEC = importlib.util.spec_from_file_location('collect_runtime', ROOT/'infra/next-phase/collect_runtime_diagnostics.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_recorded_dns_mismatch_is_not_reported_as_current_state():
    result = MODULE.historical_registry_diagnosis({'parameters': {'registryAddresses': {'value':['140.82.112.34/32']}}},
        {'value':[{'message':"CURRENT_GHCR ['140.82.114.33']\nREGISTRY_CONTROL 000"}]})
    assert result['outsideAllowlist'] == ['140.82.114.33']
    assert result['registryControlTimedOut']
    assert result['source'].startswith('historical')


def test_cloud_tail_keeps_correlation_not_prompt_or_error_body():
    request_id='a'*32
    lines=[{'Log':f'agent_request_failed status=422 category=workflow_rejected request_id={request_id}'},
        {'Log':'ValidationError: private user prompt and credentials'},
        {'Log':'The user asked a private question'},
        {'Log':'semantic_guardrail_decision '+json.dumps({'request_id':request_id,'outcome':'deny','reason':'policy_violation','prompt':'must not persist'})}]
    result = MODULE.summarize_cloud_logs('\n'.join(json.dumps(line) for line in lines))
    assert len(result['records']) == 3
    assert request_id in json.dumps(result)
    assert 'private user' not in json.dumps(result) and 'must not persist' not in json.dumps(result)


def test_validation_metadata_survives_log_collection_without_values():
    fields = [{'location':['body','messages',0,'content'],'type':'string_type'},
              {'location':['private prompt'],'type':'missing'}, {'location':['body'],'type':'missing','input':'private input'}]
    line = json.dumps({'Log':'agent_request_failed status=422 category=request_validation request_id='+'a'*32+' validation_fields='+json.dumps(fields)})
    records = MODULE.summarize_cloud_logs(line)['records']
    assert records[0]['validationFields'] == [fields[0]]
    assert 'private' not in json.dumps(records)


@pytest.mark.parametrize('variation', ['valid','digest','run','size'])
def test_guest_log_archive_requires_integrity_and_run_binding(variation):
    run='b'*32
    content=json.dumps({'runId':run,'files':{'gateway':{'tail':'token=private'}}}).encode()
    archive=gzip.compress(content)
    manifest={'runId':run,'bytes':len(archive),'sha256':hashlib.sha256(archive).hexdigest()}
    if variation=='digest': manifest['sha256']='0'*64
    if variation=='run': manifest['runId']='a'*32
    if variation=='size': manifest['bytes']+=1
    if variation=='valid':
        assert 'private' not in json.dumps(MODULE.decode_archive(archive,manifest))
    else:
        with pytest.raises(ValueError): MODULE.decode_archive(archive,manifest)


def test_missing_manifest_is_not_treated_as_empty_logs():
    with pytest.raises(ValueError, match='manifest'):
        MODULE.archive_manifest({'value':[{'message':'Run Command output truncated'}]}, 'a'*32)