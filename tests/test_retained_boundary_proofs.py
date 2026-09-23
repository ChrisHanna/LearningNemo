import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'infra/next-phase'))
SPEC = importlib.util.spec_from_file_location('boundary_proofs', ROOT / 'infra/next-phase/run_retained_boundary_proofs.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_rendered_boundary_script_has_valid_shell_syntax():
    script = MODULE.proof_script('diagnostic.azurecontainerapps.io', 'remediation.azurecontainerapps.io', 'a' * 32)
    subprocess.run(['bash', '-n'], input=script, text=True, check=True)
    assert 'sandbox delete' not in script and 'Authorization:' not in script


@pytest.mark.parametrize('change', [None, 'timeout', 'wrong-nonce', 'duplicate'])
def test_boundary_receipts_require_explicit_denial_not_timeout(change):
    nonce = 'a' * 32
    records = [('execution-demo', 'GET', '200 403'), ('execution-demo', 'POST', '200 401'), ('probe-demo', 'GET', '403 000'), ('probe-demo', 'POST', '403 000')]
    message = 'RETAINED_INVENTORY three_original_ids_ready\n' + '\n'.join(f'BOUNDARY_{nonce} {name} 998 {method} {metrics} denied resolver-azure' for name, method, metrics in records)
    if change == 'timeout':
        message = message.replace('403 000', '000 000')
    elif change == 'wrong-nonce':
        message = message.replace(nonce, 'b' * 32)
    elif change == 'duplicate':
        message += '\n' + message
    if change:
        with pytest.raises(ValueError):
            MODULE.parse_result({'value': [{'message': message}]}, nonce)
    else:
        assert MODULE.parse_result({'value': [{'message': message}]}, nonce)['status'] == 'passed'