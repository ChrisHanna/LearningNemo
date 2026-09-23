import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'infra/next-phase'))
SPEC = importlib.util.spec_from_file_location('incident_access', ROOT / 'infra/next-phase/configure_incident_workers.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize('mismatch', [None, 'target', 'caller'])
def test_worker_access_is_additive_and_scoped(mismatch):
    control = {'clientId': 'control-client', 'principalId': 'control-object'}
    incident = {'clientId': 'incident-client', 'principalId': 'incident-object'}
    app = {'name': 'ca-learningnemo-remediation-dev' if mismatch == 'target' else 'ca-learningnemo-diagnostic-dev', 'location': 'eastus', 'tags': {'owner': 'learningnemo-portfolio'}, 'identity': {'type': 'UserAssigned'},
        'properties': {'environmentId': 'fixed', 'workloadProfileName': 'Consumption', 'configuration': {'preserved': True}, 'template': {'containers': [{'image': 'pinned', 'env': [{'name': 'LEARNINGNEMO_ALLOWED_CALLER_IDS', 'value': 'control-object'}]}]}}}
    auth = {'globalValidation': {'unauthenticatedClientAction': 'Return401'}, 'identityProviders': {'azureActiveDirectory': {'enabled': True, 'validation': {'defaultAuthorizationPolicy': {'allowedApplications': ['other' if mismatch == 'caller' else 'control-client'], 'allowedPrincipals': {'identities': ['control-object']}}}}}}
    if mismatch:
        with pytest.raises(ValueError): MODULE.authorized_worker(app, auth, control, incident)
    else:
        changed = MODULE.authorized_worker(app, auth, control, incident)
        assert changed['properties']['configuration'] == app['properties']['configuration']
        assert changed['properties']['template']['containers'][0]['image'] == 'pinned'
        assert changed['properties']['template']['containers'][0]['env'][0]['value'] == 'control-object,incident-object'
        assert app['properties']['template']['containers'][0]['env'][0]['value'] == 'control-object'