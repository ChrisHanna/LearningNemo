from pathlib import Path
import subprocess
import importlib.util
import sys
import pytest


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'infra/next-phase'))
SPEC = importlib.util.spec_from_file_location('offline_repair', ROOT / 'infra/next-phase/repair_retained_workspace.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_offline_repair_checks_identity_before_writing_and_preserves_data():
    source = (ROOT / 'scripts/repair-retained-os.sh').read_text()
    subprocess.run(['bash', '-n'], input=source, text=True, check=True)
    assert source.index('vm-learningnemo-saw-dev ]]') < source.index('mount -o rw')
    assert '/dev/disk/azure/scsi1/lun0' in source
    handler = source.split("<<'HANDLER'", 1)[1].split('\nHANDLER', 1)[0]
    assert 'sandbox stop' in handler and 'sandbox delete' not in handler
    assert 'poweroff' not in handler
    assert 'sandboxes-before.txt' in source and 'sandboxes-after.txt' in source
    assert 'rm -rf' not in source


def test_helper_preview_rejects_deletion_and_wrong_scope():
    for change in [
        {'changeType': 'Delete', 'resourceId': '/any'},
        {'changeType': 'Create', 'resourceId': '/subscriptions/sub/resourceGroups/other/providers/Microsoft.Compute/virtualMachines/vm-learningnemo-repair'},
    ]:
        with pytest.raises(ValueError):
            MODULE.check_preview({'changes': [change]}, 'sub')


def test_offline_repair_refuses_running_original():
    vm = {'tags': {'owner': 'learningnemo-portfolio', 'project': 'learningnemo'}, 'instanceView': {'statuses': [{'code': 'PowerState/running'}]}}
    with pytest.raises(ValueError, match='deallocated'):
        MODULE.validate_stopped_vm(vm)