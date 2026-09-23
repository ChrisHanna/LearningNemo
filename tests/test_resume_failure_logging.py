import importlib.util
import json
from pathlib import Path
import sys

import pytest

from task_agent.console import live_workspace


ROOT = Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/'infra/next-phase'))
from diagnostic_attempt import DiagnosticAttempt
SPEC = importlib.util.spec_from_file_location('resume_logging', ROOT/'infra/next-phase/resume_workspace_sandboxes.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize('cleanup_fails', [False,True])
def test_primary_guest_failure_is_recorded_before_cleanup_and_never_replaced(tmp_path,monkeypatch,cleanup_fails):
    monkeypatch.setenv('AZURE_SUBSCRIPTION_ID','test-subscription')
    monkeypatch.setenv('LEARNINGNEMO_AZURE_APPLY','resume-owned-sandboxes')
    monkeypatch.setattr(MODULE,'az',lambda *args:{'id':'test-subscription'})
    monkeypatch.setattr(MODULE,'collect_guest',lambda *args: (_ for _ in ()).throw(ValueError('archive absent')))
    class Workspace:
        def __init__(self,subscription): pass
        def check(self): return {'readyForProbe':True}
    monkeypatch.setattr(live_workspace,'LiveWorkspace',Workspace)
    class Attempt(DiagnosticAttempt):
        def command(self,stage,arguments,**kwargs):
            self.event(stage,'started')
            if stage=='worker-endpoint':
                response={'properties':{'configuration':{'ingress':{'fqdn':'diagnostic.azurecontainerapps.io'}}}}
            elif stage=='registry-dns':
                response={'value':[{'message':'REGISTRY_DNS '+json.dumps({'ghcr.io':['140.82.112.34'],'pkg-containers.githubusercontent.com':['185.199.108.154']})}]}
            elif stage=='registry-preview': response={'changes':[]}
            elif stage=='sandbox-provision': response={'value':[{'message':'REGISTRY_CONTROL 000 primary failure'}]}
            elif stage=='workspace-query': response=[{'name':'original','etag':'dynamic'}]
            elif stage=='registry-cleanup' and cleanup_fails:
                self.record(stage,{'cleanup':'Azure refused deletion'})
                raise RuntimeError('cleanup unavailable')
            else: response={}
            self.record(stage,response)
            self.event(stage,'returned')
            return response
    attempt=Attempt(tmp_path,'sandbox-resume')
    with pytest.raises(RuntimeError,match='provisioning=failed; network cleanup='+('failed' if cleanup_fails else 'verified')):
        MODULE.run(attempt)
    events=[json.loads(line) for line in (attempt.directory/'events.jsonl').read_text().splitlines()]
    primary=next(index for index,event in enumerate(events) if event['stage']=='sandbox-provision' and event['outcome']=='failed')
    cleanup=next(index for index,event in enumerate(events) if event['stage']=='registry-cleanup' and event['outcome']=='started')
    assert primary<cleanup
    assert any('primary failure' in path.read_text() for path in attempt.directory.glob('*.json'))
    assert any(event['stage']=='guest-logs' and event['outcome']=='unavailable' for event in events)