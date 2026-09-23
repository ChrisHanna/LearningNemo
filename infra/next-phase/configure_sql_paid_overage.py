"""One-way, explicitly authorized paid continuation for the retained serverless DB."""

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path

from deploy_human_services import az


SUBSCRIPTION = '40dbf703-f68a-4ca1-b315-c37f3308c38b'
GROUP = 'rg-learningnemo-data-dev'
SERVER = 'sql-learningnemo-dev-na2ok7bgjli24'
DATABASE = 'learningnemo'
RESOURCE = f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/Microsoft.Sql/servers/{SERVER}/databases/{DATABASE}'
STATE = Path.home() / '.local/state/learningnemo'
TARGET = ('sql', 'db', 'show', '-g', GROUP, '-s', SERVER, '-n', DATABASE, '--subscription', SUBSCRIPTION)
PRESERVED = ('id', 'sku', 'minCapacity', 'maxSizeBytes', 'autoPauseDelay', 'useFreeLimit',
             'requestedBackupStorageRedundancy', 'zoneRedundant', 'tags')


def validate(database, behavior):
    if database.get('id', '').casefold() != RESOURCE.casefold():
        raise ValueError('exact retained database required')
    sku = database.get('sku', {})
    if sku.get('tier') != 'GeneralPurpose' or sku.get('family') != 'Gen5' or sku.get('capacity') != 2 or not sku.get('name', '').startswith('GP_S_Gen5'):
        raise ValueError('existing bounded serverless SKU required')
    expected = {'minCapacity': 0.5, 'maxSizeBytes': 34359738368, 'autoPauseDelay': 60,
                'useFreeLimit': True, 'freeLimitExhaustionBehavior': behavior}
    if any(database.get(key) != value for key, value in expected.items()):
        raise ValueError('serverless limits or billing policy differ')
    if database.get('tags', {}).get('owner') != 'learningnemo-portfolio':
        raise ValueError('database ownership differs')


def write_once(path, record):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
        json.dump(record, output, indent=2)
        output.flush()
        os.fsync(output.fileno())


def transition(*, apply, state=STATE, command=az):
    if os.environ.get('AZURE_SUBSCRIPTION_ID') != SUBSCRIPTION or command('account', 'show')['id'] != SUBSCRIPTION:
        raise ValueError('explicit subscription binding required')
    request = state / 'sql-paid-overage.request.json'
    current = command(*TARGET)
    if current.get('freeLimitExhaustionBehavior') != 'BillOverUsage':
        validate(current, 'AutoPause')
        if not apply or os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'sql-paid-overage-irreversible':
            raise ValueError('explicit irreversible paid-overage acknowledgement required')
        if request.exists():
            raise ValueError('prior paid transition unconfirmed; inspect Azure, do not replay')
        write_once(request, {'requestedAt': datetime.now(UTC).isoformat(), 'resourceId': RESOURCE,
                             'irreversible': True, 'before': current})
        command('sql', 'db', 'update', '-g', GROUP, '-s', SERVER, '-n', DATABASE,
                '--subscription', SUBSCRIPTION, '--free-limit-exhaustion-behavior', 'BillOverUsage')
        current = command(*TARGET)
    validate(current, 'BillOverUsage')
    before = json.loads(request.read_text())['before']
    validate(before, 'AutoPause')
    if any(current.get(key) != before.get(key) for key in PRESERVED):
        raise ValueError('paid transition changed a preserved database setting')
    receipt = state / 'sql-paid-overage.verified.json'
    if not receipt.exists():
        write_once(receipt, {'checkedAt': datetime.now(UTC).isoformat(), 'resourceId': RESOURCE,
                             'freeLimitExhaustionBehavior': 'BillOverUsage', 'idleAutoPauseMinutes': 60,
                             'preservedSettings': {key: current.get(key) for key in PRESERVED}})
    print('PASS paid serverless overage enabled; free allowance, 0.5-2 vCores, 32 GiB and idle auto-pause 60 preserved')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    transition(apply=args.apply)


if __name__ == '__main__':
    main()