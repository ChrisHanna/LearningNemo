import json
from pathlib import Path
import subprocess
import importlib.util
import sys

import pytest


@pytest.mark.parametrize('resource,change', [('natgateways/nat-learningnemo-saw-runtime-dev', 'Create'),
    ('networksecuritygroups/nsg-vnet-learningnemo-saw-dev-workspace', 'Modify'),
    ('publicipaddresses/pip-learningnemo-saw-runtime-dev', 'Delete')])
def test_runtime_preview_cannot_change_acl_or_delete_resources(resource, change):
    root = Path(__file__).parents[1]
    sys.path.insert(0, str(root / 'infra/next-phase'))
    spec = importlib.util.spec_from_file_location('runtime_egress_deploy', root / 'infra/next-phase/deploy_runtime_egress.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    preview = {'changes': [{'changeType': change, 'resourceId': '/subscriptions/test/resourceGroups/rg-learningnemo-saw-dev/providers/Microsoft.Network/' + resource}]}
    if change == 'Create':
        module.check_preview(preview, 'test')
    else:
        with pytest.raises(ValueError): module.check_preview(preview, 'test')


def test_runtime_nat_does_not_change_nsg_or_give_vm_public_ingress():
    root = Path(__file__).parents[1]
    result = subprocess.run(["az", "bicep", "build", "--file", str(root / "infra/next-phase/workspace-runtime-egress.bicep"), "--stdout"], capture_output=True, text=True, check=True)
    document = json.loads(result.stdout)
    assert document["parameters"]["attach"]["defaultValue"] is False
    assert {resource["type"] for resource in document["resources"]} == {
        "Microsoft.Network/natGateways", "Microsoft.Network/publicIPAddresses", "Microsoft.Network/virtualNetworks/subnets"}
    subnet = next(resource for resource in document["resources"] if resource["type"].endswith("/subnets"))
    assert subnet["condition"] == "[parameters('attach')]"
    assert subnet["properties"]["defaultOutboundAccess"] is False
    assert "nsg-vnet-learningnemo-saw-dev-workspace" in subnet["properties"]["networkSecurityGroup"]["id"]
    assert "rt-vnet-learningnemo-saw-dev-workspace" in subnet["properties"]["routeTable"]["id"]