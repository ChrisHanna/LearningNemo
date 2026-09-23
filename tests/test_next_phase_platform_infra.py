from __future__ import annotations

import argparse
import copy
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest


sys.dont_write_bytecode = True
ROOT = Path(__file__).parents[1]
PHASE_DIR = ROOT / "infra" / "next-phase"
PARAMETERS = PHASE_DIR / "environments" / "dev.runtime.parameters.json"
sys.path.insert(0, str(PHASE_DIR))

import check_platform_cleanup
import platform_contract
import platform_parameters
import preflight_platform
import record_platform
import runtime_parameters
import verify_platform


def load_hyphenated_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_platform = load_hyphenated_module(
    "validate_platform",
    PHASE_DIR / "validate-platform.py",
)


def safe_compiled_template() -> dict[str, object]:
    managed_environment = {
        "type": "Microsoft.App/managedEnvironments",
        "properties": {
            "infrastructureResourceGroup": "[format('mrg-{0}-container-apps-{1}', parameters('projectName'), parameters('environment'))]",
            "peerAuthentication": {"mtls": {"enabled": True}},
            "peerTrafficConfiguration": {"encryption": {"enabled": True}},
            "publicNetworkAccess": "Enabled",
            "vnetConfiguration": {
                "infrastructureSubnetId": "[resourceId('Microsoft.Network/virtualNetworks/subnets', 'vnet-test', 'snet-container-apps')]",
                "internal": False,
            },
            "workloadProfiles": [
                {"name": "Consumption", "workloadProfileType": "Consumption"}
            ],
            "zoneRedundant": False,
        },
    }
    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "variables": {
            "runtimeTags": (
                "vnet-integrated-runtime-envelope wp2a-runtime disposable "
                "expiresAt monthlyCostCeiling"
            )
        },
        "resources": [managed_environment],
    }


def identity_tags(values: dict[str, object]) -> dict[str, str]:
    return platform_contract.identity_resource_tags(values)


def runtime_tags(values: dict[str, object]) -> dict[str, str]:
    return platform_contract.runtime_resource_tags(values)


def test_platform_parameters_are_canonical_and_non_secret() -> None:
    values = runtime_parameters.parameter_values(PARAMETERS)
    assert values["containerAppsEnvironmentName"] == "cae-learningnemo-dev"
    assert values["platformResourceGroupName"] == "rg-learningnemo-platform-dev"
    assert values["monthlyCostCeiling"] == 50
    assert values["expiresAt"] == runtime_parameters.RUNTIME_EXPIRY
    assert set(values) == runtime_parameters.EXPECTED_PARAMETERS


def test_platform_parameters_reject_secret_like_field(tmp_path: Path) -> None:
    document = json.loads(PARAMETERS.read_text(encoding="utf-8"))
    document["parameters"]["clientSecret"] = {"value": "not-a-real-secret"}
    path = tmp_path / "invalid.parameters.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(runtime_parameters.ParameterError):
        runtime_parameters.load_document(path)


def test_platform_parameters_reject_managed_tag_override(tmp_path: Path) -> None:
    document = json.loads(PARAMETERS.read_text(encoding="utf-8"))
    document["parameters"]["additionalTags"]["value"]["platformPhase"] = "other"
    path = tmp_path / "invalid-tags.parameters.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(runtime_parameters.ParameterError, match="cannot override"):
        runtime_parameters.load_document(path)


def test_platform_parameters_reject_invalid_environment_name(tmp_path: Path) -> None:
    document = json.loads(PARAMETERS.read_text(encoding="utf-8"))
    document["parameters"]["containerAppsEnvironmentName"]["value"] = "Invalid_Name"
    path = tmp_path / "invalid-name.parameters.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(runtime_parameters.ParameterError, match="Container Apps environment"):
        runtime_parameters.load_document(path)


def test_runtime_expiration_materializes_privately(tmp_path: Path) -> None:
    output = tmp_path / "runtime.parameters.json"
    runtime_parameters.materialize(PARAMETERS, output, "2099-01-01T08:00:00Z")

    values = runtime_parameters.parameter_values(output, allow_runtime_expiry=False)
    assert values["expiresAt"] == "2099-01-01T08:00:00Z"
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "value",
    [
        "not-a-date",
        "2099-01-01T08:00:00",
        "2099-01-01T08:00:00+01:00",
        "2099-01-01T08:00:00.123Z",
    ],
)
def test_runtime_expiration_rejects_non_utc_or_ambiguous_values(value: str) -> None:
    with pytest.raises(runtime_parameters.ParameterError):
        runtime_parameters.parse_expiration(value, allow_runtime_expiry=False)


def test_runtime_expiration_status_is_deterministic() -> None:
    now = dt.datetime(2099, 1, 1, 8, tzinfo=dt.UTC)
    assert runtime_parameters.expiration_status("2099-01-01T07:59:59Z", now=now) == "expired"
    assert runtime_parameters.expiration_status("2099-01-01T08:00:01Z", now=now) == "active"


def test_platform_template_policy_accepts_exact_base() -> None:
    assert validate_platform.validate_template(safe_compiled_template()) == []


def test_platform_template_policy_rejects_app_workload() -> None:
    template = safe_compiled_template()
    template["resources"].append({"type": "Microsoft.App/containerApps"})

    failures = validate_platform.validate_template(template)

    assert any("unapproved resource types" in failure for failure in failures)
    assert any("paid or workload resource" in failure for failure in failures)


@pytest.mark.parametrize(
    ("property_name", "property_value", "message"),
    [
        ("vnetConfiguration", {"infrastructureSubnetId": "forbidden", "internal": False}, "delegated subnet"),
        ("appLogsConfiguration", {"destination": "log-analytics"}, "logging configuration"),
        ("peerAuthentication", {"mtls": {"enabled": False}}, "mTLS"),
    ],
)
def test_platform_template_policy_rejects_control_drift(
    property_name: str,
    property_value: object,
    message: str,
) -> None:
    template = safe_compiled_template()
    environment = template["resources"][-1]
    environment["properties"][property_name] = property_value

    failures = validate_platform.validate_template(template)

    assert any(message in failure for failure in failures)


def test_platform_inventory_classification_is_fail_closed() -> None:
    expected = {
        ("Microsoft.App/managedEnvironments", "cae-learningnemo-dev"),
        ("Microsoft.ManagedIdentity/userAssignedIdentities", "id-learningnemo-control-dev"),
    }
    complete = [{"type": resource_type, "name": name} for resource_type, name in expected]
    partial = complete[:1]
    unexpected = [*complete, {"type": "Microsoft.App/managedEnvironments", "name": "other"}]

    assert preflight_platform.classify_platform_inventory([], expected) == "empty"
    assert preflight_platform.classify_platform_inventory(partial, expected) == "partial"
    assert preflight_platform.classify_platform_inventory(complete, expected) == "complete"
    assert preflight_platform.classify_platform_inventory(unexpected, expected) == "unexpected"


def test_platform_inventory_rejects_resource_outside_exact_contract() -> None:
    values = runtime_parameters.parameter_values(PARAMETERS)
    foundation = platform_contract.foundation_inventory(values)
    resources = [
        {"type": resource_type, "name": name}
        for resource_type, name in foundation | platform_contract.wp2a_inventory(values)
    ]
    resources.append({"type": "Microsoft.Storage/storageAccounts", "name": "unreviewed"})

    assert (
        preflight_platform.classify_platform_inventory(
            resources,
            platform_contract.wp2a_inventory(values),
            foundation,
        )
        == "unexpected"
    )


def test_platform_preflight_allows_only_known_wp2b_apps_when_requested() -> None:
    values = runtime_parameters.parameter_values(PARAMETERS)
    persistent = platform_contract.foundation_inventory(values) | platform_contract.identity_inventory(values)
    runtime = platform_contract.runtime_inventory(values)
    apps = verify_platform.workload_inventory(values)
    resources = [
        {"type": resource_type, "name": name}
        for resource_type, name in persistent | runtime | apps
    ]

    assert preflight_platform.classify_platform_inventory(
        resources,
        runtime,
        persistent,
        apps,
    ) == "complete"
    resources.append({"type": "Microsoft.App/containerApps", "name": "ca-unreviewed"})
    assert preflight_platform.classify_platform_inventory(
        resources,
        runtime,
        persistent,
        apps,
    ) == "unexpected"


def test_platform_verifier_allows_only_four_expected_wp2b_apps_when_requested() -> None:
    values = runtime_parameters.parameter_values(PARAMETERS)
    base = verify_platform.expected_inventory(values, allow_workloads=False)
    with_workloads = verify_platform.expected_inventory(values, allow_workloads=True)

    assert base == platform_contract.platform_inventory(values)
    assert with_workloads - base == {
        ("microsoft.app/containerapps", f"ca-learningnemo-{mode}-dev")
        for mode in ("diagnostic", "query-runner", "remediation", "verifier")
    }


def test_budget_contract_requires_current_notified_positive_budget() -> None:
    today = dt.date(2026, 9, 12)
    budget = {
        "properties": {
            "amount": 25,
            "timePeriod": {
                "startDate": "2026-09-01T00:00:00Z",
                "endDate": "2026-12-31T00:00:00Z",
            },
            "notifications": {"actual": {"enabled": True}},
        }
    }

    assert preflight_platform.budget_is_active(budget, today)
    assert preflight_platform.budget_meets_ceiling(budget, today, 50)
    over_ceiling = copy.deepcopy(budget)
    over_ceiling["properties"]["amount"] = 51
    assert not preflight_platform.budget_meets_ceiling(over_ceiling, today, 50)
    disabled = copy.deepcopy(budget)
    disabled["properties"]["notifications"]["actual"]["enabled"] = False
    assert not preflight_platform.budget_is_active(disabled, today)
    expired = copy.deepcopy(budget)
    expired["properties"]["timePeriod"]["endDate"] = "2026-09-11T00:00:00Z"
    assert not preflight_platform.budget_is_active(expired, today)
    malformed = copy.deepcopy(budget)
    malformed["properties"]["timePeriod"]["startDate"] = "not-a-date"
    assert not preflight_platform.budget_is_active(malformed, today)


def test_platform_deployment_record_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiled = tmp_path / "platform.json"
    compiled.write_text(json.dumps(safe_compiled_template()), encoding="utf-8")
    resolved_parameters = tmp_path / "runtime.parameters.json"
    runtime_parameters.materialize(PARAMETERS, resolved_parameters, "2099-01-01T08:00:00Z")
    values = runtime_parameters.parameter_values(resolved_parameters, allow_runtime_expiry=False)
    outputs = {
        "costProfile": {"value": "VNet-integrated Consumption environment with managed network charges."},
        "platformResourceGroupName": {"value": values["platformResourceGroupName"]},
        "containerAppsEnvironmentName": {"value": values["containerAppsEnvironmentName"]},
        "infrastructureResourceGroupName": {
            "value": platform_contract.infrastructure_resource_group_name(values)
        },
        "monthlyCostCeiling": {"value": values["monthlyCostCeiling"]},
        "expiresAt": {"value": values["expiresAt"]},
    }
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps(
            {
                "name": "learningnemo-runtime-envelope-dev",
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
            "record_platform.py",
            "--deployment-result",
            str(deployment),
            "--template",
            str(compiled),
            "--parameters",
            str(resolved_parameters),
            "--parameter-source",
            str(PARAMETERS),
            "--output",
            str(output),
        ],
    )

    assert record_platform.main() == 0
    serialized = output.read_text(encoding="utf-8").casefold()
    assert "clientid" not in serialized
    assert "principalid" not in serialized
    assert "subscriptionid" not in serialized
    assert "tenantid" not in serialized


def test_cleanup_inventory_accepts_only_owned_wp1_and_wp2a_resources() -> None:
    values = runtime_parameters.parameter_values(PARAMETERS)
    project = values["projectName"]
    environment = values["environment"]
    foundation_tags = {
        "project": project,
        "environment": environment,
        "managedBy": "bicep",
        "owner": values["ownerTag"],
        "costProfile": "low-cost-poc",
        **values["additionalTags"],
    }
    identity_phase_tags = identity_tags(values)
    runtime_phase_tags = runtime_tags(values)
    resources = [
        {
            "type": "Microsoft.Network/networkSecurityGroups",
            "name": f"nsg-vnet-{project}-platform-{environment}-container-apps",
            "tags": foundation_tags,
        },
        {
            "type": "Microsoft.Network/virtualNetworks",
            "name": f"vnet-{project}-platform-{environment}",
            "tags": foundation_tags,
        },
        {
            "type": "Microsoft.App/managedEnvironments",
            "name": values["containerAppsEnvironmentName"],
            "tags": runtime_phase_tags,
        },
    ]
    resources.extend(
        {
            "type": "Microsoft.ManagedIdentity/userAssignedIdentities",
            "name": f"id-{project}-{suffix}-{environment}",
            "tags": {**identity_phase_tags, "identityPurpose": purpose},
        }
        for suffix, purpose in verify_platform.IDENTITY_PURPOSES.items()
    )

    assert check_platform_cleanup.validate_inventory(values, resources) == 1

    with pytest.raises(check_platform_cleanup.CleanupError, match="outside"):
        check_platform_cleanup.validate_inventory(
            values,
            [*resources, {"type": "Microsoft.App/containerApps", "name": "future-app", "tags": runtime_phase_tags}],
        )


def test_live_platform_verifier_accepts_expected_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_parameters = tmp_path / "runtime.parameters.json"
    runtime_parameters.materialize(PARAMETERS, resolved_parameters, "2099-01-01T08:00:00Z")
    values = runtime_parameters.parameter_values(resolved_parameters, allow_runtime_expiry=False)
    project = values["projectName"]
    environment = values["environment"]
    identity_phase_tags = identity_tags(values)
    runtime_phase_tags = runtime_tags(values)
    resource_inventory = [
        {
            "type": "Microsoft.Network/networkSecurityGroups",
            "name": f"nsg-vnet-{project}-platform-{environment}-container-apps",
        },
        {
            "type": "Microsoft.Network/virtualNetworks",
            "name": f"vnet-{project}-platform-{environment}",
        },
        {
            "type": "Microsoft.App/managedEnvironments",
            "name": values["containerAppsEnvironmentName"],
        },
        *[
            {
                "type": "Microsoft.ManagedIdentity/userAssignedIdentities",
                "name": f"id-{project}-{suffix}-{environment}",
            }
            for suffix in verify_platform.IDENTITY_PURPOSES
        ],
    ]
    group_tags = {
        "project": project,
        "environment": environment,
        "managedBy": "bicep",
        "owner": values["ownerTag"],
        "costProfile": "low-cost-poc",
        "trustZone": "trusted-platform",
        "disposable": "false",
        **values["additionalTags"],
    }

    def fake_az_json(arguments: list[str], *, timeout: int = 60):
        if arguments[:2] == ["account", "show"]:
            return {"id": "test-subscription"}
        if arguments[:2] == ["group", "show"]:
            name = arguments[arguments.index("--name") + 1]
            if name == values["platformResourceGroupName"]:
                return {"location": values["location"], "tags": group_tags}
            return {"location": values["location"], "tags": runtime_phase_tags}
        if arguments[:2] == ["resource", "list"]:
            name = arguments[arguments.index("--resource-group") + 1]
            if name == values["platformResourceGroupName"]:
                return resource_inventory
            return [
                {"type": "Microsoft.Network/loadBalancers"},
                {"type": "Microsoft.Network/publicIPAddresses"},
            ]
        raise AssertionError(f"unexpected Azure query: {arguments}")

    def fake_resource_show(
        resource_group: str,
        name: str,
        resource_type: str,
        api_version: str,
    ) -> dict[str, object]:
        assert resource_group == values["platformResourceGroupName"]
        if resource_type == "Microsoft.App/managedEnvironments":
            return {
                "location": values["location"],
                "tags": runtime_phase_tags,
                "properties": {
                    "infrastructureResourceGroup": platform_contract.infrastructure_resource_group_name(values),
                    "peerAuthentication": {"mtls": {"enabled": True}},
                    "peerTrafficConfiguration": {"encryption": {"enabled": True}},
                    "publicNetworkAccess": "Enabled",
                    "vnetConfiguration": {
                        "infrastructureSubnetId": (
                            "/subscriptions/test-subscription/resourceGroups/"
                            f"{values['platformResourceGroupName']}/providers/Microsoft.Network/"
                            f"virtualNetworks/vnet-{project}-platform-{environment}/"
                            "subnets/snet-container-apps"
                        ),
                        "internal": False,
                    },
                    "workloadProfiles": [
                        {"name": "Consumption", "workloadProfileType": "Consumption"}
                    ],
                    "zoneRedundant": False,
                },
            }
        suffix = next(
            suffix
            for suffix in verify_platform.IDENTITY_PURPOSES
            if name == f"id-{project}-{suffix}-{environment}"
        )
        return {
            "location": values["location"],
            "tags": {
                **identity_phase_tags,
                "identityPurpose": verify_platform.IDENTITY_PURPOSES[suffix],
            },
            "properties": {"isolationScope": "Regional"},
        }

    monkeypatch.setattr(verify_platform, "az_json", fake_az_json)
    monkeypatch.setattr(verify_platform, "resource_show", fake_resource_show)
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "test-subscription")
    monkeypatch.setattr(sys, "argv", ["verify_platform.py", "--parameters", str(resolved_parameters)])

    assert verify_platform.main() == 0


def test_platform_mutations_require_subscription_and_acknowledgement() -> None:
    deploy = (PHASE_DIR / "deploy-platform.sh").read_text(encoding="utf-8")
    remove = (PHASE_DIR / "remove-platform.sh").read_text(encoding="utf-8")

    assert 'mode="what-if"' in deploy
    assert "AZURE_SUBSCRIPTION_ID" in deploy
    assert "LEARNINGNEMO_AZURE_APPLY" in deploy
    assert "platform-runtime-envelope" in deploy
    assert "--ttl-hours" in deploy
    assert "--require-budget" in deploy
    assert "summarize_what_if.py" in deploy
    assert "XDG_STATE_HOME" in deploy
    assert "AZURE_SUBSCRIPTION_ID" in remove
    assert "LEARNINGNEMO_AZURE_DELETE" in remove
    assert "platform-runtime-envelope" in remove
    assert "--expired-only" in remove
    assert "check_platform_cleanup.py" in remove
    assert "snapshot_foundation.py" in remove