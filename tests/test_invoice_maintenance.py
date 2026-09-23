from pathlib import Path
import sys

import pytest


def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'infra/next-phase'))
    import install_invoice_cache
    return install_invoice_cache


def test_mirror_addresses_must_be_bounded_and_public(monkeypatch):
    maintenance = module(monkeypatch)
    for response in ({'value': []}, {'value': [{'message': 'PACKAGE_MIRROR ["127.0.0.1"]'}]}):
        with pytest.raises(ValueError):
            maintenance.mirror_addresses(response)
    assert maintenance.mirror_addresses({'value': [{'message': 'PACKAGE_MIRROR ["52.147.219.192"]'}]}) == ['52.147.219.192/32']


def test_maintenance_preview_cannot_modify_existing_policy(monkeypatch):
    maintenance = module(monkeypatch)
    for change in ('Delete', 'Modify', 'Create'):
        with pytest.raises(ValueError):
            maintenance.validate_preview({'changes': [{'changeType': change, 'resourceId': '/other/resource'}]}, 'subscription')
    assert 'every sandbox must be stopped' in maintenance.PREFLIGHT


def test_image_cache_uses_dedicated_paths_without_recursive_ownership_change():
    import subprocess
    root = Path(__file__).resolve().parents[1]
    script = root / 'scripts/install-invoice-image-cache.sh'
    subprocess.run(['bash', '-n', str(script)], check=True)
    source = script.read_text()
    assert 'cache_directory=/var/lib/learningnemo-invoice-cache' in source
    for variable in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'CONTAINERS_STORAGE_CONF'):
        assert 'Environment=' + variable + '=' in source
    assert 'chown -R' not in source and 'chmod -R' not in source
    assert 'http://localhost/v4.0.0/libpod/info' in source
    assert 'trap cleanup_cache EXIT' in source
    assert 'chown sawadmin:sawadmin "$storage_config"' in source
    assert 'run_user test -r "$storage_config"' in source
    assert 'ExecStart=/usr/bin/podman --root $cache_directory/data/overlay-storage --runroot /run/user/$admin_uid/learningnemo-invoice-cache/run --storage-driver overlay system service' in source
    assert 'systemctl --user disable --now podman.socket podman.service' in source


def test_cache_verifier_is_read_only():
    import subprocess
    root = Path(__file__).resolve().parents[1]
    script = root / 'scripts/verify-invoice-image-cache.sh'
    subprocess.run(['bash', '-n', str(script)], check=True)
    source = script.read_text()
    assert '/v1.40/images/json' in source
    assert '--unix-socket "$socket"' in source
    for command in ('sandbox create', 'sandbox delete', 'podman pull', 'systemctl restart', 'chmod ', 'chown '):
        assert command not in source