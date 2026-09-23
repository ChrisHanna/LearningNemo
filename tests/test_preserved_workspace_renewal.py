from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'infra/next-phase'))
SPEC = importlib.util.spec_from_file_location('preserved_renewal', ROOT / 'infra/next-phase/renew_preserved_workspace.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_preserved_renewal_rearms_shutdown_without_replacing_policy():
    script = MODULE.timer_script(datetime.now(UTC) + timedelta(minutes=45), 'a' * 32)
    assert '__EXPIRY__' not in script and '__NONCE__' not in script
    assert 'NextElapseUSecRealtime' in script
    assert 'cp -a' in script
    assert 'sandbox delete' not in script
    assert 'systemctl poweroff' not in script
    assert 'openshell-gateway' not in script


def test_expiry_preserves_sandboxes_until_user_tests():
    for name in ('scripts/bootstrap-saw-openshell.sh', 'scripts/renew-saw-timer.sh'):
        script = (ROOT / name).read_text()
        expiry = script.split('cat > /usr/local/sbin/learningnemo-saw-expire', 1)[1].split('chmod 0700', 1)[0]
        assert 'sandbox stop' in expiry
        assert 'sandbox delete' not in expiry
        assert 'systemctl poweroff' not in expiry


@pytest.mark.parametrize('minutes', [-1, 2, 121])
def test_renewal_rejects_invalid_lifetime(minutes):
    with pytest.raises(ValueError):
        MODULE.timer_script(datetime.now(UTC) + timedelta(minutes=minutes), 'a' * 32)


@pytest.mark.parametrize('fails', [False, True])
def test_startup_metadata_restores_original_network_even_when_boot_fails(fails):
    calls = []
    rules = [{'name': 'deny-imds', 'access': 'Deny', 'etag': 'old'}]
    class Attempt:
        run_id = 'a' * 32
        def command(self, stage, arguments, **kwargs):
            calls.append(stage)
            if stage == 'metadata-preview':
                return {'changes': []}
            if stage == 'metadata-apply':
                assert arguments[:4] == ['network', 'nsg', 'rule', 'delete']
                assert arguments[-1] == 'deny-sandbox-host-imds'
            if stage == 'metadata-cleanup':
                assert arguments[:3] == ['deployment', 'group', 'create']
            if stage == 'network-after':
                return [{**rules[0], 'etag': 'new'}]
        def record(self, *args):
            pass
        def event(self, *args):
            calls.append('verified')
    def operation():
        with MODULE.startup_metadata(Attempt(), 'sub', rules):
            calls.append('boot')
            if fails:
                raise RuntimeError('boot failed')
    if fails:
        with pytest.raises(RuntimeError, match='boot failed'):
            operation()
    else:
        operation()
    assert calls == ['metadata-preview', 'metadata-apply', 'boot', 'metadata-cleanup', 'network-after', 'verified']


def test_metadata_maintenance_restores_the_existing_runtime_template():
    template = (ROOT / 'infra/next-phase/workspace-runtime-lock.bicep').read_text()
    assert "destinationAddressPrefix: 'AzurePlatformIMDS'" in template
    assert "access: 'Deny'" in template
    assert "priority: 121" in template