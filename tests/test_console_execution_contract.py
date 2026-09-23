import subprocess
import sys

import pytest

from task_agent.console.execution_contract import ExecutionStatus
from task_agent.control.canonical import content_hash
from task_agent.control.execution import VerifiedRecovery
from task_agent.control.models import OperationReceipt


def test_cloud_console_imports_without_control_package():
    source = '''
import importlib.abc,sys
class DenyControl(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'task_agent.control' or fullname.startswith('task_agent.control.'):
            raise ImportError('dashboard must not import control')
sys.meta_path.insert(0,DenyControl())
import task_agent.console.cloud
import task_agent.console.cloud_controller
'''
    subprocess.run([sys.executable, '-c', source], check=True, capture_output=True, text=True)


@pytest.mark.parametrize('tampered', [False, True])
def test_console_receipts_match_control_wire_hashes(tampered):
    execution_id, plan_hash = 'execution-' + 'a' * 32, 'b' * 64
    broker = OperationReceipt(operation_id='remediate.activate-cycle-safe-query-v1', result_code='safe_query_activated', result={'safe_query_version': 'cycle-safe-v1'})
    verification = VerifiedRecovery(execution_id=execution_id, plan_hash=plan_hash, safe_query_version='cycle-safe-v1',
        checks={'safe_query_version_active': True, 'no_owned_query_running': True, 'deterministic_result': True})
    payload = dict(execution_id=execution_id, plan_id='plan-test', plan_hash=plan_hash, state='completed', broker=broker.model_dump(mode='json'),
        broker_hash=content_hash(broker), verification=verification.model_dump(mode='json'), verification_hash=content_hash(verification))
    if tampered:
        payload['verification']['checks']['deterministic_result'] = False
        with pytest.raises(ValueError): ExecutionStatus.model_validate(payload)
    else:
        assert ExecutionStatus.model_validate(payload).model_dump(mode='json') == payload