from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest


sys.dont_write_bytecode = True
ROOT = Path(__file__).parents[1]
PHASE_DIR = ROOT / "infra" / "next-phase"
PARAMETERS = PHASE_DIR / "environments" / "dev.parameters.json"
sys.path.insert(0, str(PHASE_DIR))

import check_toolchain
import check_foundation_cleanup
import foundation_parameters
import preflight
import record_foundation
import snapshot_foundation
import verify_foundation


def load_hyphenated_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_foundation = load_hyphenated_module(
    "validate_foundation",
    PHASE_DIR / "validate-foundation.py",
)


def network_args(**overrides: str) -> argparse.Namespace:
    values = foundation_parameters.parameter_values(PARAMETERS)
    arguments = {
        "platform_vnet": values["platformVnetAddressPrefix"],
        "container_apps_subnet": values["containerAppsSubnetPrefix"],
        "private_endpoint_subnet": values["privateEndpointSubnetPrefix"],
        "saw_vnet": values["sawVnetAddressPrefix"],
        "saw_workspace_subnet": values["sawWorkspaceSubnetPrefix"],
        "saw_firewall_subnet": values["sawFirewallSubnetPrefix"],
    }
    arguments.update(overrides)
    return argparse.Namespace(**arguments)


def safe_compiled_template() -> dict[str, object]:
    rules = [
        {"name": name, "properties": copy.deepcopy(properties)}
        for name, properties in validate_foundation.REQUIRED_RULE_PROPERTIES.items()
    ]
    return {
        "$schema": "https://schema.management.azure.com/schemas/2018-05-01/subscriptionDeploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "resources": [
            {"type": "Microsoft.Resources/resourceGroups"},
            {"type": "Microsoft.Resources/deployments"},
            {"type": "Microsoft.Network/virtualNetworks"},
            {"type": "Microsoft.Network/routeTables"},
            {
                "type": "Microsoft.Network/networkSecurityGroups",
                "properties": {"securityRules": rules},
            },
        ],
    }


def test_canonical_parameters_validate_and_materialize(tmp_path: Path) -> None:
    source = foundation_parameters.load_document(PARAMETERS)
    assert source["parameters"]["expiresOn"]["value"] == foundation_parameters.RUNTIME_EXPIRY

    output = tmp_path / "resolved.parameters.json"
    foundation_parameters.materialize(PARAMETERS, output, "2099-01-01")

    values = foundation_parameters.parameter_values(output, allow_runtime_expiry=False)
    assert values["expiresOn"] == "2099-01-01"
    assert values["sawVnetAddressPrefix"] == "10.50.0.0/16"


def test_parameter_contract_rejects_unexpected_fields(tmp_path: Path) -> None:
    document = json.loads(PARAMETERS.read_text(encoding="utf-8"))
    document["parameters"]["clientSecret"] = {"value": "not-a-real-secret"}
    path = tmp_path / "invalid.parameters.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(foundation_parameters.ParameterError):
        foundation_parameters.load_document(path)


def test_parameter_contract_rejects_managed_tag_override(tmp_path: Path) -> None:
    document = json.loads(PARAMETERS.read_text(encoding="utf-8"))
    document["parameters"]["additionalTags"]["value"]["expiresOn"] = "never"
    path = tmp_path / "invalid-tags.parameters.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(foundation_parameters.ParameterError, match="cannot override"):
        foundation_parameters.load_document(path)


def test_parameter_contract_rejects_unsafe_owner_alias(tmp_path: Path) -> None:
    document = json.loads(PARAMETERS.read_text(encoding="utf-8"))
    document["parameters"]["ownerTag"]["value"] = "unsafe'query"
    path = tmp_path / "invalid-owner.parameters.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(foundation_parameters.ParameterError, match="safe name characters"):
        foundation_parameters.load_document(path)


def test_network_contract_accepts_canonical_topology() -> None:
    config = preflight.validate_networks(network_args())
    assert str(config.container_apps_subnet) == "10.40.0.0/27"
    assert str(config.saw_firewall_subnet) == "10.50.255.0/26"


@pytest.mark.parametrize(
    "overrides",
    [
        {"saw_vnet": "10.40.0.0/16"},
        {"container_apps_subnet": "10.40.0.0/28"},
        {"saw_firewall_subnet": "10.50.255.0/27"},
        {"private_endpoint_subnet": "10.40.0.16/28"},
    ],
)
def test_network_contract_rejects_unsafe_topology(overrides: dict[str, str]) -> None:
    with pytest.raises(preflight.PreflightError):
        preflight.validate_networks(network_args(**overrides))


def test_compiled_template_policy_accepts_foundation_only() -> None:
    failures, resource_types = validate_foundation.validate_template(safe_compiled_template())
    assert failures == []
    assert "Microsoft.Compute/virtualMachines" not in resource_types


def test_compiled_template_policy_rejects_metered_resource() -> None:
    template = safe_compiled_template()
    template["resources"].append({"type": "Microsoft.Compute/virtualMachines"})

    failures, _resource_types = validate_foundation.validate_template(template)

    assert any("unapproved resource types" in failure for failure in failures)
    assert any("metered resource type" in failure for failure in failures)


def test_compiled_template_policy_rejects_weakened_sql_rule() -> None:
    template = safe_compiled_template()
    nsg = next(
        resource
        for resource in template["resources"]
        if resource["type"] == "Microsoft.Network/networkSecurityGroups"
    )
    sql_rule = next(
        rule
        for rule in nsg["properties"]["securityRules"]
        if rule["name"] == "deny-direct-azure-sql"
    )
    sql_rule["properties"]["access"] = "Allow"

    failures, _resource_types = validate_foundation.validate_template(template)

    assert any("deny-direct-azure-sql" in failure for failure in failures)


def test_toolchain_contract_has_parseable_minimums() -> None:
    contract = check_toolchain.load_contract(PHASE_DIR / "toolchain.json")
    for value in contract["minimum"].values():
        assert check_toolchain.version_tuple(value) > (0, 0, 0)


def test_provider_contract_covers_all_planned_work_packages() -> None:
    contract = json.loads((PHASE_DIR / "provider-phases.json").read_text(encoding="utf-8"))
    assert set(contract["phases"]) == {
        "foundation",
        "cost",
        "identities",
        "platform",
        "platform-services",
        "database",
            "artifacts",
        "workspace",
    }
    assert "Microsoft.Network" in contract["phases"]["foundation"]
    assert contract["phases"]["cost"] == [
        "Microsoft.Consumption",
        "Microsoft.Resources",
    ]
    assert contract["phases"]["identities"] == [
        "Microsoft.ManagedIdentity",
        "Microsoft.Resources",
    ]
    assert contract["phases"]["platform"] == [
        "Microsoft.App",
        "Microsoft.Resources",
    ]
    assert "Microsoft.ContainerRegistry" in contract["phases"]["platform-services"]
    assert "Microsoft.Sql" in contract["phases"]["database"]
    assert "Microsoft.Compute" in contract["phases"]["workspace"]


@pytest.mark.parametrize(
    ("actual", "expected", "classification"),
    [
        (set(), {("type", "one")}, "empty"),
        ({("type", "one")}, {("type", "one"), ("type", "two")}, "partial"),
        ({("type", "one")}, {("type", "one")}, "complete"),
        ({("type", "one"), ("future", "two")}, {("type", "one")}, "complete"),
        ({("type", "other")}, {("type", "one")}, "unexpected"),
    ],
)
def test_partial_foundation_state_is_classified(
    actual: set[tuple[str, str]],
    expected: set[tuple[str, str]],
    classification: str,
) -> None:
    assert preflight.classify_resource_inventory(actual, expected) == classification


def test_deployment_record_contains_hashes_and_no_account_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = tmp_path / "resolved.parameters.json"
    foundation_parameters.materialize(PARAMETERS, resolved, "2099-01-01")
    compiled = tmp_path / "foundation.json"
    compiled.write_text(json.dumps(safe_compiled_template()), encoding="utf-8")
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps(
            {
                "name": "learningnemo-foundation-dev",
                "properties": {
                    "provisioningState": "Succeeded",
                    "outputs": {
                        "costProfile": {"value": "low-cost-poc-no-metered-compute"},
                        "platformVnetName": {"value": "vnet-learningnemo-platform-dev"},
                        "containerAppsSubnetId": {
                            "value": "/subscriptions/test/resourceGroups/test/providers/"
                            "Microsoft.Network/virtualNetworks/test/subnets/test"
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "manifest.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "record_foundation.py",
            "--deployment-result",
            str(deployment),
            "--template",
            str(compiled),
            "--parameters",
            str(resolved),
            "--parameter-source",
            str(PARAMETERS),
            "--output",
            str(output),
        ],
    )

    assert record_foundation.main() == 0
    record = json.loads(output.read_text(encoding="utf-8"))
    assert len(record["templateSha256"]) == 64
    assert len(record["resolvedParametersSha256"]) == 64
    assert "subscription" not in json.dumps(record).casefold()
    assert "tenant" not in json.dumps(record).casefold()
    assert "containerAppsSubnetId" not in record["outputs"]


def test_predelete_snapshot_is_minimal_and_excludes_unknown_tags() -> None:
    snapshot = snapshot_foundation.build_snapshot(
        {
            "name": "rg-learningnemo-saw-dev",
            "location": "eastus",
            "tags": {
                "project": "learningnemo",
                "expiresOn": "2099-01-01",
                "unreviewedNote": "must-not-be-copied",
            },
        },
        [
            {
                "id": "/subscriptions/sensitive-id/resource",
                "name": "vnet-learningnemo-saw-dev",
                "type": "Microsoft.Network/virtualNetworks",
                "location": "eastus",
                "properties": {"not": "captured"},
            }
        ],
    )

    serialized = json.dumps(snapshot)
    assert "sensitive-id" not in serialized
    assert "must-not-be-copied" not in serialized
    assert "properties" not in serialized
    assert snapshot["resourceGroup"]["tags"]["expiresOn"] == "2099-01-01"


def test_foundation_cleanup_requires_exact_names_and_tags() -> None:
    values = foundation_parameters.parameter_values(PARAMETERS)
    common_tags = {
        "project": values["projectName"],
        "environment": values["environment"],
        "managedBy": "bicep",
        "owner": values["ownerTag"],
        "costProfile": "low-cost-poc",
        **values["additionalTags"],
    }
    group = {
        "location": values["location"],
        "tags": {
            **common_tags,
            "trustZone": "trusted-platform",
            "disposable": "false",
        },
    }
    resources = [
        {
            "type": "Microsoft.Network/networkSecurityGroups",
            "name": f"nsg-vnet-{values['projectName']}-platform-{values['environment']}-container-apps",
            "tags": common_tags,
        },
        {
            "type": "Microsoft.Network/virtualNetworks",
            "name": f"vnet-{values['projectName']}-platform-{values['environment']}",
            "tags": common_tags,
        },
    ]

    check_foundation_cleanup.validate_group_state(values, "platform", group, resources)
    with pytest.raises(check_foundation_cleanup.CleanupError, match="exact WP1"):
        check_foundation_cleanup.validate_group_state(
            values,
            "platform",
            group,
            [
                *resources,
                {
                    "type": "Microsoft.Network/virtualNetworks",
                    "name": "unreviewed-vnet",
                    "tags": common_tags,
                },
            ],
        )


def test_foundation_cleanup_rejects_malformed_expiration() -> None:
    with pytest.raises(check_foundation_cleanup.CleanupError, match="valid ISO date"):
        check_foundation_cleanup.parse_expiration("2099-13-99")


def test_live_verifier_helpers_fail_closed() -> None:
    assert verify_foundation.tags_match({"environment": "dev"}, {"environment": "dev"})
    assert not verify_foundation.tags_match({}, {"environment": "dev"})
    with pytest.raises(verify_foundation.VerificationError):
        verify_foundation.subnet_by_name({"subnets": []}, "snet-workspace")


def test_live_verifier_accepts_exact_expected_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = tmp_path / "resolved.parameters.json"
    foundation_parameters.materialize(PARAMETERS, resolved, "2099-01-01")
    values = foundation_parameters.parameter_values(resolved, allow_runtime_expiry=False)
    project = values["projectName"]
    environment = values["environment"]
    platform_group = values["platformResourceGroupName"]
    saw_group = values["sawResourceGroupName"]
    platform_vnet = f"vnet-{project}-platform-{environment}"
    saw_vnet = f"vnet-{project}-saw-{environment}"
    platform_nsg = f"nsg-{platform_vnet}-container-apps"
    saw_nsg = f"nsg-{saw_vnet}-workspace"
    route_table = f"rt-{saw_vnet}-workspace"
    common_tags = {
        "project": project,
        "environment": environment,
        "managedBy": "bicep",
        "owner": values["ownerTag"],
        "costProfile": "low-cost-poc",
        **values["additionalTags"],
    }
    platform_tags = {**common_tags, "trustZone": "trusted-platform", "disposable": "false"}
    saw_tags = {
        **common_tags,
        "trustZone": "untrusted-agent-workspace",
        "disposable": "true",
        "expiresOn": "2099-01-01",
        "sawMaturity": "network-foundation-only",
    }

    def linked(name: str) -> dict[str, str]:
        return {"id": f"/subscriptions/test/resourceGroups/test/providers/Microsoft.Network/{name}"}

    platform_resources = [
        {"type": "Microsoft.Network/networkSecurityGroups", "name": platform_nsg, "tags": common_tags},
        {"type": "Microsoft.Network/virtualNetworks", "name": platform_vnet, "tags": common_tags},
    ]
    saw_resources = [
        {"type": "Microsoft.Network/networkSecurityGroups", "name": saw_nsg, "tags": saw_tags},
        {"type": "Microsoft.Network/routeTables", "name": route_table, "tags": saw_tags},
        {"type": "Microsoft.Network/virtualNetworks", "name": saw_vnet, "tags": saw_tags},
    ]
    platform_vnet_state = {
        "addressSpace": {"addressPrefixes": [values["platformVnetAddressPrefix"]]},
        "subnets": [
            {
                "name": "snet-container-apps",
                "addressPrefix": values["containerAppsSubnetPrefix"],
                "networkSecurityGroup": linked(platform_nsg),
                "delegations": [{"serviceName": "Microsoft.App/environments"}],
            },
            {
                "name": "snet-private-endpoints",
                "addressPrefix": values["privateEndpointSubnetPrefix"],
                "privateEndpointNetworkPolicies": "Disabled",
            },
        ],
    }
    saw_vnet_state = {
        "addressSpace": {"addressPrefixes": [values["sawVnetAddressPrefix"]]},
        "virtualNetworkPeerings": [],
        "subnets": [
            {
                "name": "snet-workspace",
                "addressPrefix": values["sawWorkspaceSubnetPrefix"],
                "networkSecurityGroup": linked(saw_nsg),
                "routeTable": linked(route_table),
            },
            {
                "name": "AzureFirewallSubnet",
                "addressPrefix": values["sawFirewallSubnetPrefix"],
            },
        ],
    }
    nsg_state = {
        "securityRules": [
            {"name": name, **copy.deepcopy(properties)}
            for name, properties in validate_foundation.REQUIRED_RULE_PROPERTIES.items()
        ]
    }

    def fake_az_json(arguments: list[str], *, timeout: int = 45):
        command = tuple(arguments[:3])
        if arguments[:2] == ["account", "show"]:
            return {"id": "test-subscription"}
        if arguments[:2] == ["group", "show"]:
            name = arguments[arguments.index("--name") + 1]
            return {
                "location": values["location"],
                "tags": platform_tags if name == platform_group else saw_tags,
            }
        if arguments[:2] == ["resource", "list"]:
            name = arguments[arguments.index("--resource-group") + 1]
            return platform_resources if name == platform_group else saw_resources
        if command == ("network", "vnet", "show"):
            name = arguments[arguments.index("--name") + 1]
            return platform_vnet_state if name == platform_vnet else saw_vnet_state
        if command == ("network", "nsg", "show"):
            return nsg_state
        if command == ("network", "route-table", "show"):
            return {"disableBgpRoutePropagation": True, "routes": []}
        raise AssertionError(f"unexpected Azure query: {arguments}")

    monkeypatch.setattr(verify_foundation, "az_json", fake_az_json)
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "test-subscription")
    monkeypatch.setattr(
        sys,
        "argv",
        ["verify_foundation.py", "--parameters", str(resolved)],
    )

    assert verify_foundation.main() == 0


def test_mutating_scripts_require_subscription_and_acknowledgement() -> None:
    deploy = (PHASE_DIR / "deploy-foundation.sh").read_text(encoding="utf-8")
    register = (PHASE_DIR / "register-providers.sh").read_text(encoding="utf-8")
    remove = (PHASE_DIR / "remove-foundation.sh").read_text(encoding="utf-8")

    assert 'mode="what-if"' in deploy
    assert "AZURE_SUBSCRIPTION_ID" in deploy
    assert "LEARNINGNEMO_AZURE_APPLY" in deploy
    assert "XDG_STATE_HOME" in deploy
    assert "summarize_what_if.py" in deploy
    assert "AZURE_SUBSCRIPTION_ID" in register
    assert "LEARNINGNEMO_AZURE_APPLY" in register
    assert "AZURE_SUBSCRIPTION_ID" in remove
    assert "LEARNINGNEMO_AZURE_DELETE" in remove
    assert "XDG_STATE_HOME" in remove
    assert "check_foundation_cleanup.py" in remove