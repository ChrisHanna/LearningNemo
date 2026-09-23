import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('sandbox_resume', ROOT / 'infra/next-phase/resume_workspace_sandboxes.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize('case', ['valid', 'private', 'metadata', 'host', 'missing', 'duplicate'])
def test_bootstrap_uses_only_bounded_vm_observed_registry_ips(case):
    value = {'ghcr.io': ['140.82.114.33'], 'pkg-containers.githubusercontent.com': ['185.199.108.154']}
    if case == 'private': value['ghcr.io'] = ['10.40.0.4']
    if case == 'metadata': value['ghcr.io'] = ['169.254.169.254']
    if case == 'host': value['other.test'] = value.pop('ghcr.io')
    if case == 'missing': value['ghcr.io'] = []
    receipt = 'REGISTRY_DNS ' + json.dumps(value)
    response = {'value': [{'message': receipt + ('\n' + receipt if case == 'duplicate' else '')}]}
    if case == 'valid':
        assert MODULE.registry_addresses(response) == ['140.82.114.33/32', '185.199.108.154/32']
    else:
        with pytest.raises(ValueError): MODULE.registry_addresses(response)


def test_registry_host_pin_matches_the_rule_and_has_bounded_cleanup():
    import subprocess
    source = (ROOT / 'scripts/resume-saw-sandboxes.sh').read_text()
    value = {'ghcr.io': ['140.82.112.34'], 'pkg-containers.githubusercontent.com': ['185.199.108.154']}
    rendered, addresses = MODULE.pin_registry(source, {'value': [{'message': 'REGISTRY_DNS ' + json.dumps(value)}]}, 'a'*32)
    assert '140.82.112.34 ghcr.io # learningnemo-registry-pin-' in rendered
    assert '140.82.112.34/32' in addresses
    assert '__GHCR_IP__' not in rendered and '__PIN_ID__' not in rendered
    assert 'trap cleanup EXIT' in rendered
    assert 'existing_registry_host_override' in rendered
    assert 'cp -a /etc/hosts' in rendered
    subprocess.run(['bash', '-n'], input=rendered, text=True, check=True)