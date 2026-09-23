import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('retention', ROOT / 'infra/next-phase/retention.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_retention_hold_blocks_cleanup_even_with_malformed_record(tmp_path):
    MODULE.require_cleanup_released(tmp_path)
    (tmp_path / 'testing-retention.json').write_text('incomplete')
    with pytest.raises(ValueError, match='retained for owner testing'):
        MODULE.require_cleanup_released(tmp_path)


def test_retained_vm_cannot_boot_into_old_deletion_handler(tmp_path):
    MODULE.require_safe_retained_boot(tmp_path, running=False)
    (tmp_path / 'testing-retention.json').write_text('{}')
    MODULE.require_safe_retained_boot(tmp_path, running=True)
    with pytest.raises(ValueError, match='old destructive expiry handler offline'):
        MODULE.require_safe_retained_boot(tmp_path, running=False)


def test_offline_receipt_must_match_the_actual_boot_disk(tmp_path):
    (tmp_path / 'testing-retention.json').write_text('{}')
    record = {'vmId': '/vm', 'diskId': '/repaired', 'originalDiskId': '/original', 'expiryDeletesWorkspaces': False, 'bootTimerDisabled': True, 'handlerSha256': 'a' * 64}
    (tmp_path / 'offline-retention.verified.json').write_text(json.dumps(record))
    vm = {'id': '/vm', 'storageProfile': {'osDisk': {'managedDisk': {'id': '/repaired'}}}}
    MODULE.require_safe_retained_boot(tmp_path, running=False, vm=vm)
    vm['storageProfile']['osDisk']['managedDisk']['id'] = '/original'
    with pytest.raises(ValueError):
        MODULE.require_safe_retained_boot(tmp_path, running=False, vm=vm)
    with pytest.raises(ValueError):
        MODULE.require_cleanup_released(tmp_path)