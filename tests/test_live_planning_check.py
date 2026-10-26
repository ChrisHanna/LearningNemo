import importlib.util
import json
import os
from pathlib import Path
import stat

import pytest


def script():
    path=Path(__file__).resolve().parents[1]/'scripts/check-live-invoice-planning.py'
    spec=importlib.util.spec_from_file_location('live_planning_check',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(os.name!='posix',reason='Operational helper runs in the Linux Azure CLI environment')
def test_acceptance_state_is_private_before_replacement(tmp_path):
    module=script()
    module.STATE=tmp_path/'state.json'
    module.STATE.write_text('{}')
    module.STATE.chmod(0o644)
    module.save({'job_submitted':True,'cookies':{'test':'fixture'}})
    assert stat.S_IMODE(module.STATE.stat().st_mode)==0o600
    assert json.loads(module.STATE.read_text())['job_submitted'] is True


@pytest.mark.skipif(os.name!='posix',reason='Operational helper uses POSIX no-follow protection')
def test_acceptance_state_cannot_follow_symlink(tmp_path):
    module=script()
    target=tmp_path/'target.json'
    target.write_text('retained')
    module.STATE=tmp_path/'state.json'
    module.STATE.symlink_to(target)
    with pytest.raises(OSError):
        module.save({'cookies':{'test':'fixture'}})
    assert target.read_text()=='retained'


def test_existing_acceptance_request_cannot_be_launched_again(tmp_path,monkeypatch):
    module=script()
    path=tmp_path/'state.json'
    path.write_text('{"job_submitted":true}')
    monkeypatch.setattr('sys.argv',['check-live-invoice-planning.py','start','--state',str(path)])
    with pytest.raises(RuntimeError,match='not replay'):
        module.main()
    assert json.loads(path.read_text())=={'job_submitted':True}