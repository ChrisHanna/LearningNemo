"""Validate the fixed cloud demo template before publishing its dashboard."""

import json
import sys
from pathlib import Path


def validate(template):
    modules = {item["name"]: item for item in template["resources"]}
    module = modules["learningnemo-cloud-demo-apps"]["properties"]["template"]
    services = module["variables"]["services"]
    assert len(services) == 3
    assert [item["external"] for item in services] == [True, False, False]
    assert [item["port"] for item in services] == [8080, 8080, 8001]
    apps = module["resources"][0]["properties"]
    assert apps["configuration"]["ingress"]["allowInsecure"] is False
    assert apps["configuration"]["secrets"] == []
    assert apps["template"]["scale"] == {"minReplicas": 1, "maxReplicas": 1}
    workspace = modules["learningnemo-cloud-demo-workspace-access"]["properties"]["template"]
    roles = [item for item in workspace["resources"] if item["type"] == "Microsoft.Authorization/roleDefinitions"]
    assert len(roles) == 2
    actions = [action for role in roles for action in role["properties"]["permissions"][0]["actions"]]
    assert all(action.endswith("/read") or action == "Microsoft.Compute/virtualMachines/runCommand/action" for action in actions)
    assert not any("*" in action for action in actions)
    assert "Local Azure CLI" not in json.dumps(template)
    secret = modules["learningnemo-cloud-demo-secret-access"]["properties"]["template"]
    assert "llm-gateway-client-key" in json.dumps(secret)
    assert "openai-api-key" not in json.dumps(secret)


if __name__ == "__main__":
    validate(json.loads(Path(sys.argv[1]).read_text()))
    print("PASS HTTPS-only dashboard, private controller/agent, bounded scale, and narrow identity grants")