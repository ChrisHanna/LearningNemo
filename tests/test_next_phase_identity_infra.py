from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


sys.dont_write_bytecode = True
ROOT = Path(__file__).parents[1]
PHASE_DIR = ROOT / "infra" / "next-phase"
PARAMETERS = PHASE_DIR / "environments" / "dev.platform.parameters.json"
sys.path.insert(0, str(PHASE_DIR))

import platform_contract
import platform_parameters
import preflight_identities
import record_identities
import verify_identities


def load_hyphenated_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_identities = load_hyphenated_module(
    "validate_identities",
    PHASE_DIR / "validate-identities.py",
)


def safe_identity_template() -> dict[str, object]:
    resources = []
    for suffix, purpose in platform_contract.IDENTITY_PURPOSES.items():
        resources.append(
            {
                "type": "Microsoft.ManagedIdentity/userAssignedIdentities",
                "name": f"id-learningnemo-{suffix}-dev",
                "tags": {
                    "costProfile": "identity-only-no-compute",
                    "platformPhase": "wp2a-identities",
                    "plannedRuntimeEnvironment": "cae-learningnemo-dev",
                    "monthlyCostCeiling": "50",
                    "identityPurpose": purpose,
                },
                "properties": {"isolationScope": "Regional"},
            }
        )
    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "resources": resources,
    }


def test_identity_template_policy_accepts_six_regional_identities() -> None:
    assert validate_identities.validate_template(safe_identity_template()) == []


def test_identity_template_policy_rejects_runtime_resource() -> None:
    template = safe_identity_template()
    template["resources"].append({"type": "Microsoft.App/managedEnvironments"})

    failures = validate_identities.validate_template(template)

    assert any("unapproved" in failure for failure in failures)


def test_identity_state_classifies_recovery_and_rejects_unknown() -> None:
    values = platform_parameters.parameter_values(PARAMETERS)
    foundation = platform_contract.foundation_inventory(values)
    identities = platform_contract.identity_inventory(values)
    foundation_resources = [
        {"type": resource_type, "name": name}
        for resource_type, name in foundation
    ]
    identity_resources = [
        {"type": resource_type, "name": name}
        for resource_type, name in identities
    ]

    assert preflight_identities.classify_identity_state(values, foundation_resources) == "empty"
    assert preflight_identities.classify_identity_state(
        values,
        [*foundation_resources, identity_resources[0]],
    ) == "partial"
    assert preflight_identities.classify_identity_state(
        values,
        [*foundation_resources, *identity_resources],
    ) == "complete"
    assert preflight_identities.classify_identity_state(
        values,
        [*foundation_resources, {"type": "Microsoft.Compute/virtualMachines", "name": "unexpected"}],
    ) == "unexpected"


def test_identity_record_contains_only_safe_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = platform_parameters.parameter_values(PARAMETERS)
    template = tmp_path / "identities.json"
    template.write_text(json.dumps(safe_identity_template()), encoding="utf-8")
    outputs = {
        "costProfile": {"value": "identity-only-no-compute"},
        "platformResourceGroupName": {"value": values["platformResourceGroupName"]},
        "plannedRuntimeEnvironmentName": {"value": values["containerAppsEnvironmentName"]},
        "monthlyCostCeiling": {"value": values["monthlyCostCeiling"]},
        "identityNames": {
            "value": [
                f"id-{values['projectName']}-{suffix}-{values['environment']}"
                for suffix in platform_contract.IDENTITY_PURPOSES
            ]
        },
    }
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps(
            {
                "name": "learningnemo-platform-identities-dev",
                "properties": {"provisioningState": "Succeeded", "outputs": outputs},
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "manifest.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "record_identities.py",
            "--deployment-result",
            str(deployment),
            "--template",
            str(template),
            "--parameters",
            str(PARAMETERS),
            "--parameter-source",
            str(PARAMETERS),
            "--output",
            str(output),
        ],
    )

    assert record_identities.main() == 0
    serialized = output.read_text(encoding="utf-8").casefold()
    assert "/subscriptions/" not in serialized
    assert "clientid" not in serialized
    assert "principalid" not in serialized


def test_live_identity_verifier_accepts_exact_identity_slice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = platform_parameters.parameter_values(PARAMETERS)
    foundation = platform_contract.foundation_inventory(values)
    identities = platform_contract.identity_inventory(values)
    inventory = [
        {"type": resource_type, "name": name}
        for resource_type, name in foundation | identities
    ]

    def fake_az_json(arguments: list[str], *, timeout: int = 60):
        if arguments[:2] == ["account", "show"]:
            return {"id": "test-subscription"}
        if arguments[:2] == ["group", "show"]:
            return {
                "location": values["location"],
                "tags": platform_contract.platform_group_tags(values),
            }
        if arguments[:2] == ["resource", "list"]:
            return inventory
        if arguments[:2] == ["resource", "show"]:
            name = arguments[arguments.index("--name") + 1]
            suffix = next(
                suffix
                for suffix in platform_contract.IDENTITY_PURPOSES
                if name == f"id-{values['projectName']}-{suffix}-{values['environment']}"
            )
            return {
                "location": values["location"],
                "tags": {
                    **platform_contract.identity_resource_tags(values),
                    "identityPurpose": platform_contract.IDENTITY_PURPOSES[suffix],
                },
                "properties": {"isolationScope": "Regional"},
            }
        raise AssertionError(f"unexpected Azure query: {arguments}")

    monkeypatch.setattr(verify_identities, "az_json", fake_az_json)
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "test-subscription")
    monkeypatch.setattr(sys, "argv", ["verify_identities.py", "--parameters", str(PARAMETERS)])

    assert verify_identities.main() == 0


def test_identity_mutations_require_subscription_and_acknowledgement() -> None:
    deploy = (PHASE_DIR / "deploy-identities.sh").read_text(encoding="utf-8")
    remove = (PHASE_DIR / "remove-identities.sh").read_text(encoding="utf-8")

    assert 'mode="what-if"' in deploy
    assert "AZURE_SUBSCRIPTION_ID" in deploy
    assert "LEARNINGNEMO_AZURE_APPLY" in deploy
    assert "platform-identities" in deploy
    assert "summarize_what_if.py" in deploy
    assert "record_identities.py" in deploy
    assert "verify_identities.py" in deploy
    assert "AZURE_SUBSCRIPTION_ID" in remove
    assert "LEARNINGNEMO_AZURE_DELETE" in remove
    assert "platform-identities" in remove
    assert "remove the runtime envelope" in remove