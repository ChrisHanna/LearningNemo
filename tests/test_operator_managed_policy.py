import importlib.util
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('availability_policy',Path(__file__).parents[1]/'infra/next-phase/availability_policy.py')
policy=importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def test_transition_preserves_ownership_and_removes_only_expiry():
    original={'tags':{'owner':'learningnemo-portfolio','project':'learningnemo','purpose':'test','expiresAt':'old'}}
    tags=policy.managed_tags(original)
    assert tags['purpose']=='test' and 'expiresAt' not in tags and tags['disposable']=='false'
    assert original['tags']['expiresAt']=='old'
    assert policy.dependency_mode([{'tags':tags}]*3)=='operator-managed'
    assert policy.dependency_mode([original]*3)=='leased'
    with pytest.raises(ValueError):policy.dependency_mode([original,{'tags':tags}])
    with pytest.raises(ValueError):policy.managed_tags({'tags':{'owner':'other'}})


def test_host_transition_is_non_destructive_and_keeps_run_timers(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'infra/next-phase'))
    from configure_invoice_availability import host_script
    source=host_script(True,True).split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
    compile(source,'<host-policy>','exec')
    assert "['systemctl','disable','--now','learningnemo-saw-expire.timer']" in source
    assert 'invoice-expire-' not in source and "cli('sandbox','stop'" not in source
    assert 'delete' not in source and "item['phase']=='Stopped'" in source
    assert "'admission_enabled':True" in source
    assert "'admission_enabled':False" in host_script(False,True)


def test_old_timed_deployments_refuse_to_replace_managed_policy(tmp_path):
    import os
    import subprocess
    (tmp_path/'invoice-availability.policy.json').write_text('{"mode":"operator-managed"}')
    root=Path(__file__).parents[1]
    for filename in ('deploy-platform.sh','deploy-artifacts.sh','deploy-database-network.sh'):
        path=root/'infra/next-phase'/filename
        result=subprocess.run(['bash',str(path),'--apply'],env={**os.environ,'LEARNINGNEMO_INFRA_STATE_DIR':str(tmp_path)},capture_output=True,text=True)
        assert result.returncode==1 and 'Operator-managed invoice availability' in result.stderr