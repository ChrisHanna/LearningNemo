from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
PHASE_DIR = ROOT / "infra" / "next-phase"
CONFIG = PHASE_DIR / "environments" / "dev.artifacts.config.json"
sys.path.insert(0, str(PHASE_DIR))

import artifact_parameters


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_artifacts", PHASE_DIR / "validate-artifacts.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_artifacts = load_validator()


def test_artifact_parameters_materialize_expiry_without_generated_values(tmp_path: Path) -> None:
    config = artifact_parameters.load_config(CONFIG)
    assert "registryName" not in config
    output = tmp_path / "artifacts.parameters.json"

    artifact_parameters.materialize(CONFIG, output, "2026-09-12T20:00:00Z")

    values = artifact_parameters.parameter_values(output)
    assert values["expiresAt"] == "2026-09-12T20:00:00Z"
    assert values["monthlyCostCeiling"] == 50
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_artifact_template_policy_accepts_minimal_registry() -> None:
    registry = {
        "type": "Microsoft.ContainerRegistry/registries",
        "sku": {"name": "Basic"},
        "properties": {
            "adminUserEnabled": False,
            "anonymousPullEnabled": False,
            "dataEndpointEnabled": False,
            "publicNetworkAccess": "Enabled",
        },
    }
    assignments = [
        {
            "type": "Microsoft.Authorization/roleAssignments",
            "properties": {
                "principalType": "ServicePrincipal",
                "roleDefinitionId": "7f951dda-4ed3-4680-a7ca-43fe172d538d",
            },
        }
        for _ in range(5)
    ]
    template = {
        "resources": [
            {"type": "Microsoft.Resources/resourceGroups"},
            {
                "type": "Microsoft.Resources/deployments",
                "properties": {"template": {"resources": [registry, *assignments]}},
            },
        ],
        "metadata": "expiring-basic-container-registry wp3-artifacts expiresAt AcrPull",
    }

    assert validate_artifacts.validate(template) == []

    registry["properties"]["adminUserEnabled"] = True
    assert "artifact registry access or cost controls differ" in validate_artifacts.validate(template)


def test_invoice_pull_scope_is_exact_and_owned(monkeypatch):
    import pytest
    import verify_artifacts
    tags = {"owner": "learningnemo-portfolio", "project": "learningnemo", "purpose": "invoice-agent-workflow"}
    calls = []
    def query(arguments, **kwargs):
        calls.append(arguments)
        return {"tags": tags.copy(), "principalId": arguments[-1]}
    monkeypatch.setattr(verify_artifacts, "az_json", query)
    principals = verify_artifacts.invoice_pull_principals()
    assert len(principals) == 5
    assert not any("simulator" in principal for principal in principals)
    assert len(calls) == 6
    tags["owner"] = "unrelated"
    with pytest.raises(verify_artifacts.ArtifactVerificationError, match="ownership"):
        verify_artifacts.invoice_pull_principals()