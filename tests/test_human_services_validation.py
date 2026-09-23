from copy import deepcopy
from datetime import UTC, datetime, timedelta
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("human_service_validation", ROOT / "infra/next-phase/validate_human_services.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
NOW = datetime(2026, 9, 15, 5, tzinfo=UTC)


@pytest.fixture(scope="module")
def template():
    result = subprocess.run(["az", "bicep", "build", "--file", str(ROOT / "infra/next-phase/human-services.bicep"), "--stdout"], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def parameters():
    values = {"location": "eastus", "environmentId": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-learningnemo-platform-dev/providers/Microsoft.App/managedEnvironments/cae-learningnemo-dev",
        "registryServer": "fixtureacr.azurecr.io", "image": "fixtureacr.azurecr.io/learningnemo/cloud-human@sha256:" + "a" * 64,
        "expiresAt": (NOW + timedelta(hours=1)).isoformat(), "tenantId": SUBSCRIPTION, "apiClientId": SUBSCRIPTION,
        "publicClientId": SUBSCRIPTION, "sqlServer": "fixture-sql.database.windows.net", "sqlDatabase": "fixture", "deployApps": False}
    return {"parameters": {name: {"value": value} for name, value in values.items()}}


@pytest.mark.parametrize("change", ["valid", "public", "mutable", "expiry", "grant", "scale", "subscription", "entrypoint"])
def test_human_service_deployment_fails_closed(template, change):
    document = deepcopy(template)
    inputs = parameters()
    app = next(item for item in document["resources"] if item["type"] == "Microsoft.App/containerApps")
    if change == "public": app["properties"]["configuration"]["ingress"]["external"] = True
    if change == "mutable": inputs["parameters"]["image"]["value"] = "fixtureacr.azurecr.io/learningnemo/cloud-human:latest"
    if change == "expiry": inputs["parameters"]["expiresAt"]["value"] = (NOW + timedelta(hours=3)).isoformat()
    if change == "grant": document["resources"].append({"type": "Microsoft.Authorization/roleAssignments"})
    if change == "scale": app["properties"]["template"]["scale"]["maxReplicas"] = 4
    if change == "subscription": inputs["parameters"]["environmentId"]["value"] = "another-subscription"
    if change == "entrypoint": app["properties"]["template"]["containers"][0]["command"] = ["sh"]
    if change == "valid":
        MODULE.validate(document, inputs, subscription=SUBSCRIPTION, now=NOW)
    else:
        with pytest.raises(ValueError): MODULE.validate(document, inputs, subscription=SUBSCRIPTION, now=NOW)