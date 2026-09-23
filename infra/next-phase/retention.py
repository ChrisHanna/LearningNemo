"""Hold demo resources until the owner explicitly releases the testing hold."""

import json
import os
from pathlib import Path


def require_cleanup_released(state: Path):
    if (state / 'testing-retention.json').exists():
        raise ValueError('Resources retained for owner testing; explicitly release the testing hold before cleanup')


def require_safe_retained_boot(state: Path, *, running: bool, vm: dict | None = None):
    if not running and (state / 'testing-retention.json').exists():
        try:
            record = json.loads((state / 'offline-retention.verified.json').read_text())
            disk_id = vm['storageProfile']['osDisk']['managedDisk']['id']
            if (record['vmId'].casefold() == vm['id'].casefold()
                    and record['diskId'].casefold() == disk_id.casefold()
                    and record['originalDiskId'].casefold() != disk_id.casefold()
                    and record['expiryDeletesWorkspaces'] is False and record['bootTimerDisabled'] is True
                    and len(record['handlerSha256']) == 64):
                return
        except (OSError, ValueError, KeyError, TypeError):
            pass
        raise ValueError('Retained VM boot blocked: inspect and replace the old destructive expiry handler offline before using a reviewed startup procedure')


def main():
    import argparse
    from datetime import UTC, datetime
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['hold', 'check'])
    args = parser.parse_args()
    state = Path(os.environ.get('LEARNINGNEMO_INFRA_STATE_DIR', str(Path.home() / '.local/state/learningnemo')))
    if args.action == 'check':
        require_cleanup_released(state)
        return
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state / 'testing-retention.json'
    if not path.exists():
        with open(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as stream:
            json.dump({'requestedAt': datetime.now(UTC).isoformat(), 'reason': 'Owner must test before resources are deleted', 'cleanupAllowed': False}, stream)
    print('PASS testing retention hold active; resource cleanup blocked')


if __name__ == '__main__':
    main()