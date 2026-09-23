"""Renew admission leases without deploying staged code or changing permissions."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import os

from deploy_human_services import az, save, GROUP
from preflight_platform import validate_budget


def lease_only_template(template, kind, expiry):
    updated = deepcopy(template)
    matches = [entry for container in updated['containers'] for entry in container['env']
               if entry['name'] == f'LEARNINGNEMO_{kind.upper()}_EXPIRES_AT']
    if len(matches) != 1 or 'value' not in matches[0]:
        raise ValueError('one literal admission deadline required')
    matches[0]['value'] = expiry
    return updated


def main():
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'renew-running-human':
        raise ValueError('explicit running-service renewal acknowledgement required')
    account = az('account', 'show')
    if account['id'] != os.environ['AZURE_SUBSCRIPTION_ID']:
        raise ValueError('subscription mismatch')
    validate_budget(account, required=True, maximum_amount=50)
    dependencies = [az('containerapp', 'env', 'show', '-g', 'rg-learningnemo-platform-dev', '-n', 'cae-learningnemo-dev'),
                    az('group', 'show', '-n', 'rg-learningnemo-data-network-dev'),
                    az('acr', 'list', '-g', 'rg-learningnemo-artifacts-dev')[0]]
    if any(item.get('tags', {}).get('owner') != 'learningnemo-portfolio' for item in dependencies):
        raise ValueError('dependency ownership differs')
    now = datetime.now(UTC)
    expiry = min(now + timedelta(minutes=100), *[datetime.fromisoformat(item['tags']['expiresAt'].replace('Z', '+00:00')) for item in dependencies])
    if expiry <= now + timedelta(minutes=15):
        raise ValueError('dependency lease too short')
    deadline = expiry.replace(microsecond=0).isoformat()
    apps = az('containerapp', 'list', '-g', GROUP)
    for kind in ('incident', 'review', 'execution'):
        app = next((item for item in apps if item['name'] == f'ca-learningnemo-{kind}-dev'), None)
        if app is None:
            continue
        if app['tags'].get('owner') != 'learningnemo-portfolio' or app['properties']['configuration']['ingress']['external']:
            raise ValueError('unexpected public or unowned human service')
        name = app['name']
        save(f'{kind}-lease-before-{now.strftime("%Y%m%dT%H%M%S")}.json', app)
        expected = lease_only_template(app['properties']['template'], kind, deadline)
        az('containerapp', 'update', '-g', GROUP, '-n', name, '--set-env-vars', f'LEARNINGNEMO_{kind.upper()}_EXPIRES_AT={deadline}')
        current = az('containerapp', 'show', '-g', GROUP, '-n', name)
        if (current['properties']['template'] != expected or current['identity'] != app['identity']
                or current['properties']['configuration'] != app['properties']['configuration']):
            raise ValueError('renewal changed more than the admission deadline')
        az('tag', 'update', '--resource-id', app['id'], '--operation', 'Merge', '--tags', 'expiresAt=' + deadline)
        print(f'PASS {kind} admission renewed to {deadline}; deployed image and authority unchanged', flush=True)
    save('running-human-renewal.verified.json', {'expiresAt': deadline, 'checkedAt': datetime.now(UTC).isoformat()})


if __name__ == '__main__':
    main()