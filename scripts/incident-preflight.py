#!/usr/bin/env python3
"""Read-only prerequisite report for a real cloud incident rehearsal."""

from datetime import UTC, datetime
import argparse
import json
import os
from pathlib import Path
import subprocess


def az(*args):
    result = subprocess.run(["az", *args, "--output", "json", "--only-show-errors"], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--network', action='store_true')
    args = parser.parse_args()
    now = datetime.now(UTC)
    state = Path.home() / ".local/state/learningnemo"
    account = az("account", "show")
    if account["id"] != os.environ["AZURE_SUBSCRIPTION_ID"]:
        raise ValueError("selected subscription differs")
    print("UTC", now.isoformat(), flush=True)
    if args.network:
        for operation in ('show-effective-route-table', 'list-effective-nsg'):
            result = az('network', 'nic', operation, '-g', 'rg-learningnemo-saw-dev', '-n', 'nic-vm-learningnemo-saw-dev')
            if operation == 'show-effective-route-table':
                for route in result.get('value', []):
                    print('ROUTE', route.get('name'), route.get('state'), route.get('source'), route.get('addressPrefix'), route.get('nextHopType'), route.get('nextHopIpAddress'), flush=True)
            else:
                for group in result.get('value', []):
                    for rule in group.get('effectiveSecurityRules', []):
                        print('RULE', rule.get('name'), rule.get('priority'), rule.get('access'), rule.get('direction'), rule.get('destinationAddressPrefix'), rule.get('destinationPortRange'), flush=True)
        subnet = az('network','vnet','subnet','show','-g','rg-learningnemo-saw-dev','--vnet-name','vnet-learningnemo-saw-dev','-n','snet-workspace')
        print('SUBNET', json.dumps({name: subnet.get(name) for name in ('natGateway','routeTable','defaultOutboundAccess')}), flush=True)
        return
    expired = []
    for name in ("foundation-dev.parameters.json", "runtime-dev.parameters.json", "database-network-dev.parameters.json", "artifacts-dev.state.json", "workloads-dev.parameters.json"):
        document = json.loads((state / name).read_text())
        parameters = document.get("parameters", {})
        value = parameters.get("expiresAt", {}).get("value") or document.get("expiresAt")
        if value:
            expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
            status = "VALID" if expiry > now else "EXPIRED"
            if status == "EXPIRED": expired.append(name)
        else:
            value = parameters.get("expiresOn", {}).get("value")
            status = "DATE-BOUND"
        print(status, name, value, flush=True)
    vm = az("vm", "get-instance-view", "-g", "rg-learningnemo-saw-dev", "-n", "vm-learningnemo-saw-dev")
    print("SAW", [item["code"] for item in vm.get("instanceView", {}).get("statuses", [])], flush=True)
    registry_parameters = state / 'workspace-registry-bootstrap.parameters.json'
    if registry_parameters.exists():
        print('REGISTRY_EXCEPTION_ADDRESSES', json.loads(registry_parameters.read_text())['parameters']['registryAddresses']['value'], flush=True)
    rules = az('network','nsg','rule','list','-g','rg-learningnemo-saw-dev','--nsg-name','nsg-vnet-learningnemo-saw-dev-workspace')
    print('BOOTSTRAP_RULE_PRESENT', any(rule['name'] == 'allow-bootstrap-ghcr-https' for rule in rules), flush=True)
    for name in ('workspace-renewal.verified.json', 'workspace-renewal.error.json'):
        path = state / name
        if path.exists():
            document = json.loads(path.read_text())
            print('RENEWAL', name, json.dumps(document)[:1500], flush=True)
        else:
            print('RENEWAL', name, 'not recorded', flush=True)
    process = subprocess.run(['ps', '-eo', 'pid,etime,args'], capture_output=True, text=True, check=True)
    for line in process.stdout.splitlines():
        if 'renew_preserved_workspace.py' in line or 'resume_workspace_sandboxes.py' in line or 'az vm run-command' in line or 'az vm deallocate' in line:
            print('MAINTENANCE', line[:200], flush=True)
    for name in ('workspace-resume-sandboxes.result.json', 'workspace-runtime-egress.proof.json'):
        path = state / name
        if path.exists():
            value = json.loads(path.read_text())
            print('PROOF', name, 'updated', datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(), flush=True)
            if 'status' in value:
                print(json.dumps({'status': value['status'], 'receipt': value.get('receipt')}), flush=True)
            else:
                print('\n'.join(item.get('message','') for item in value.get('value',[]))[-1700:], flush=True)
    for group in ("rg-learningnemo-demo-dev", "rg-learningnemo-human-dev"):
        for app in az("containerapp", "list", "-g", group):
            print("APP", app["name"], app["properties"].get("runningStatus"), app["tags"].get("expiresAt"), flush=True)
    print("BLOCKING_LEASES", json.dumps(expired), flush=True)
    print("INFO no resource was changed; readiness is not a completed human or sandbox run", flush=True)


if __name__ == "__main__":
    main()