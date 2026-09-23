"""Authorize only the private Incident MI at diagnostic and query-runner."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import os

from deploy_human_services import ROOT, STATE, az, save, parameters
from preflight_platform import validate_budget


GROUP = 'rg-learningnemo-platform-dev'


def writable(value):
    if isinstance(value, dict):
        return {key: writable(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [writable(item) for item in value]
    return value


def authorized_worker(app, auth, control, incident, *, mode='incident'):
    targets = {'incident': ('diagnostic', 'query-runner'), 'execution': ('remediation', 'verifier')}[mode]
    if app['name'] not in tuple(f'ca-learningnemo-{service}-dev' for service in targets) or app.get('tags', {}).get('owner') != 'learningnemo-portfolio':
        raise ValueError('unexpected worker target')
    desired = {'name': app['name'], 'location': app['location'], 'tags': deepcopy(app['tags']), 'identity': deepcopy(app['identity']),
        'properties': {key: deepcopy(app['properties'][key]) for key in ('environmentId', 'workloadProfileName', 'configuration', 'template')}, 'auth': deepcopy(auth)}
    desired = writable(desired)
    desired['identity'] = {'type': desired['identity']['type'], 'userAssignedIdentities': {identifier: {} for identifier in desired['identity'].get('userAssignedIdentities', {})}}
    configuration = desired['properties']['configuration']
    if 'ingress' in configuration:
        configuration['ingress'].pop('fqdn', None)
        for key in ('transport', 'clientCertificateMode'):
            if key in configuration['ingress']:
                configuration['ingress'][key] = configuration['ingress'][key].lower()
    configuration.pop('targetLabel', None)
    for registry in configuration.get('registries', []):
        for key in ('passwordSecretRef', 'username'):
            if not registry.get(key): registry.pop(key, None)
    desired['properties']['template'].pop('revisionSuffix', None)
    for container in desired['properties']['template']['containers']:
        container.pop('imageType', None)
        container.get('resources', {}).pop('ephemeralStorage', None)
    aad = desired['auth']['identityProviders']['azureActiveDirectory']
    if not aad['enabled'] or desired['auth']['globalValidation']['unauthenticatedClientAction'] != 'Return401':
        raise ValueError('worker authentication posture differs')
    policy = aad['validation']['defaultAuthorizationPolicy']
    for container, key, expected in ((policy, 'allowedApplications', 'clientId'), (policy['allowedPrincipals'], 'identities', 'principalId')):
        if set(container[key]) not in ({control[expected]}, {control[expected], incident[expected]}):
            raise ValueError('unexpected worker caller authorization')
        container[key] = [control[expected], incident[expected]]
    claims = aad['validation'].get('jwtClaimChecks', {})
    if 'allowedClientApplications' in claims:
        if set(claims['allowedClientApplications']) not in ({control['clientId']}, {control['clientId'], incident['clientId']}):
            raise ValueError('unexpected JWT application allowlist')
        claims['allowedClientApplications'] = [control['clientId'], incident['clientId']]
    containers = desired['properties']['template']['containers']
    if len(containers) != 1:
        raise ValueError('unexpected worker container inventory')
    entry = next(item for item in containers[0]['env'] if item['name'] == 'LEARNINGNEMO_ALLOWED_CALLER_IDS')
    if set(entry['value'].split(',')) not in ({control['principalId']}, {control['principalId'], incident['principalId']}):
        raise ValueError('runtime caller allowlist differs')
    entry['value'] = control['principalId'] + ',' + incident['principalId']
    if mode == 'incident' and app['name'] == 'ca-learningnemo-query-runner-dev':
        policy['allowedApplications'] = [control['clientId']]
        policy['allowedPrincipals']['identities'] = [control['principalId']]
        if 'allowedClientApplications' in claims:
            claims['allowedClientApplications'] = [control['clientId']]
        entry['value'] = control['principalId']
    return desired


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('incident', 'execution'), default='incident')
    mode = parser.parse_args().mode
    if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != mode + '-worker-access':
        raise ValueError('explicit worker authorization acknowledgement required')
    account = az('account', 'show')
    if account['id'] != os.environ['AZURE_SUBSCRIPTION_ID']:
        raise ValueError('subscription mismatch')
    validate_budget(account, required=True, maximum_amount=50)
    control = az('identity', 'show', '-g', GROUP, '-n', 'id-learningnemo-control-dev')
    incident = az('identity', 'show', '-g', 'rg-learningnemo-human-dev', '-n', f'id-learningnemo-{mode}-dev')
    workers, audiences, snapshots = [], {}, {}
    for service in (('diagnostic', 'query-runner') if mode == 'incident' else ('remediation', 'verifier')):
        app = az('containerapp', 'show', '-g', GROUP, '-n', f'ca-learningnemo-{service}-dev')
        if datetime.fromisoformat(app['tags']['expiresAt'].replace('Z', '+00:00')) < datetime.now(UTC) + timedelta(minutes=20):
            raise ValueError('worker lease too short')
        auth = az('rest', '--method', 'GET', '--url', app['id'] + '/authConfigs/current?api-version=2025-01-01')['properties']
        snapshots[service] = {'app': app, 'auth': auth}
        workers.append(authorized_worker(app, auth, control, incident, mode=mode))
        audiences[service] = auth['identityProviders']['azureActiveDirectory']['registration']['clientId']
    save(f'{mode}-workers.before.json', snapshots)
    path = save(f'{mode}-workers.parameters.json', parameters({'workers': workers}))
    template = str(ROOT / 'infra/next-phase/incident-worker-access.bicep')
    preview = az('deployment', 'group', 'what-if', '-g', GROUP, '-n', 'learningnemo-incident-workers', '--template-file', template, '--parameters', '@' + str(path), '--no-pretty-print')
    save('incident-workers.what-if.json', preview)
    allowed_ids = {snapshot['app']['id'].lower() for snapshot in snapshots.values()}
    allowed_ids |= {identifier + '/authconfigs/current' for identifier in allowed_ids}
    for change in preview['changes']:
        if change['changeType'] in ('NoChange', 'Ignore'): continue
        if change['changeType'] != 'Modify' or change['resourceId'].lower() not in allowed_ids:
            raise ValueError('worker authorization preview exceeds intended resources')
    az('deployment', 'group', 'create', '-g', GROUP, '-n', 'learningnemo-incident-workers', '--template-file', template, '--parameters', '@' + str(path))
    for expected in workers:
        app = az('containerapp', 'show', '-g', GROUP, '-n', expected['name'])
        auth = az('rest', '--method', 'GET', '--url', app['id'] + '/authConfigs/current?api-version=2025-01-01')['properties']
        current = authorized_worker(app, auth, control, incident, mode=mode)
        if current != expected:
            raise ValueError('worker configuration changed beyond expected authorization')
        policy = auth['identityProviders']['azureActiveDirectory']['validation']['defaultAuthorizationPolicy']
        permitted = not (mode == 'incident' and app['name'] == 'ca-learningnemo-query-runner-dev')
        if (incident['clientId'] in policy['allowedApplications']) != permitted or (incident['principalId'] in policy['allowedPrincipals']['identities']) != permitted:
            raise ValueError('incident authorization not installed')
        actual_env = {item['name']: item.get('value') for item in app['properties']['template']['containers'][0]['env']}
        if (incident['principalId'] in actual_env['LEARNINGNEMO_ALLOWED_CALLER_IDS'].split(',')) != permitted:
            raise ValueError('incident runtime authorization not installed')
    save(f'{mode}-workers.verified.json', {'subscription': account['id'], 'audiences': audiences, 'incidentClientId': incident['clientId'], 'incidentObjectId': incident['principalId'], 'verifiedAt': datetime.now(UTC).isoformat()})
    print(f'PASS {mode} worker authority verified; Incident has no query-runner access; image and network unchanged', flush=True)


if __name__ == '__main__':
    main()