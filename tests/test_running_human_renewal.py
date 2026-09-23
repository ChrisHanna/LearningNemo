import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'infra/next-phase'))
SPEC = importlib.util.spec_from_file_location('human_renewal', ROOT / 'infra/next-phase/renew_running_human_services.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_only_admission_deadline_changes():
    original = {'containers': [{'image': 'existing-pinned-image', 'env': [{'name': 'LEARNINGNEMO_INCIDENT_EXPIRES_AT', 'value': 'old'}, {'name': 'AZURE_CLIENT_ID', 'value': 'fixed'}]}], 'scale': {'maxReplicas': 1}}
    result = MODULE.lease_only_template(original, 'incident', 'new')
    assert original['containers'][0]['env'][0]['value'] == 'old'
    result['containers'][0]['env'][0]['value'] = 'old'
    assert result == original
    with pytest.raises(ValueError): MODULE.lease_only_template(original, 'execution', 'new')