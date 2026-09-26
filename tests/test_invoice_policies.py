"""Invoice sandbox policies follow OpenShell v0.0.116 security guidance."""

from pathlib import Path
import re

import pytest
import yaml


ROOT = Path(__file__).parents[1]
KINDS = ('planning', 'execution')


def policy(kind):
    return yaml.safe_load((ROOT / f'infra/next-phase/openshell/invoice-{kind}-policy.yaml').read_text())


@pytest.mark.parametrize('kind', KINDS)
def test_landlock_is_a_hard_requirement(kind):
    assert policy(kind)['landlock'] == {'compatibility': 'hard_requirement'}


@pytest.mark.parametrize('kind', KINDS)
def test_only_the_agent_interpreter_reaches_inspected_enforced_routes(kind):
    for network in policy(kind)['network_policies'].values():
        assert network['binaries'] == [{'path': '/usr/local/bin/python3.12'}]
        for endpoint in network['endpoints']:
            assert endpoint['protocol'] == 'rest' and endpoint['enforcement'] == 'enforce'
            assert endpoint['rules'] and 'access' not in endpoint
            assert 'tls' not in endpoint, 'tls: terminate is deprecated and has no effect; TLS is auto-detected'


def test_image_guarantees_every_landlock_path():
    dockerfile = (ROOT / 'containers/invoice-agent.Dockerfile').read_text()
    guarded = set(re.search(r'RUN for path in ([^;]+); do test -e', dockerfile).group(1).split())
    for kind in KINDS:
        filesystem = policy(kind)['filesystem_policy']
        assert set(filesystem['read_only']) | set(filesystem['read_write']) <= guarded
        for network in policy(kind)['network_policies'].values():
            assert {binary['path'] for binary in network['binaries']} <= guarded
