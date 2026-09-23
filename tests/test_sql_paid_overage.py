import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def billing(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'infra/next-phase'))
    module = importlib.import_module('configure_sql_paid_overage')
    monkeypatch.setenv('AZURE_SUBSCRIPTION_ID', module.SUBSCRIPTION)
    monkeypatch.setenv('LEARNINGNEMO_AZURE_APPLY', 'sql-paid-overage-irreversible')
    return module


def database(billing):
    return {'id': billing.RESOURCE, 'sku': {'name': 'GP_S_Gen5', 'tier': 'GeneralPurpose', 'family': 'Gen5', 'capacity': 2},
            'minCapacity': 0.5, 'maxSizeBytes': 34359738368, 'autoPauseDelay': 60, 'useFreeLimit': True,
            'freeLimitExhaustionBehavior': 'AutoPause', 'tags': {'owner': 'learningnemo-portfolio'}}


def test_paid_transition_mutates_once_preserves_limits_and_verifies_readback(billing, tmp_path):
    current = database(billing)
    calls = []
    def command(*args):
        calls.append(args)
        if args[0] == 'account':
            return {'id': billing.SUBSCRIPTION}
        if args[2] == 'update':
            assert json.loads((tmp_path / 'sql-paid-overage.request.json').read_text())['before'] == current
            current['freeLimitExhaustionBehavior'] = 'BillOverUsage'
        return dict(current)
    billing.transition(apply=True, state=tmp_path, command=command)
    billing.transition(apply=False, state=tmp_path, command=command)
    writes = [args for args in calls if args[:3] == ('sql', 'db', 'update')]
    assert len(writes) == 1 and writes[0][-2:] == ('--free-limit-exhaustion-behavior', 'BillOverUsage')
    assert (tmp_path / 'sql-paid-overage.verified.json').is_file()


def test_lost_paid_transition_is_not_retried(billing, tmp_path):
    calls = []
    def command(*args):
        if args[0] == 'account':
            return {'id': billing.SUBSCRIPTION}
        if args[2] == 'update':
            calls.append(args)
            raise TimeoutError('unknown Azure outcome')
        return database(billing)
    with pytest.raises(TimeoutError):
        billing.transition(apply=True, state=tmp_path, command=command)
    with pytest.raises(ValueError, match='do not replay'):
        billing.transition(apply=True, state=tmp_path, command=command)
    assert len(calls) == 1


@pytest.mark.parametrize('field,value', [('autoPauseDelay', -1), ('minCapacity', 1), ('useFreeLimit', False), ('id', 'other')])
def test_paid_transition_rejects_changed_limits(billing, field, value):
    current = database(billing)
    current[field] = value
    with pytest.raises(ValueError):
        billing.validate(current, 'AutoPause')


def test_paid_transition_requires_explicit_acknowledgement(billing, monkeypatch, tmp_path):
    monkeypatch.delenv('LEARNINGNEMO_AZURE_APPLY')
    def command(*args):
        assert args[:3] != ('sql', 'db', 'update')
        return {'id': billing.SUBSCRIPTION} if args[0] == 'account' else database(billing)
    with pytest.raises(ValueError, match='acknowledgement'):
        billing.transition(apply=True, state=tmp_path, command=command)
    assert not list(tmp_path.iterdir())