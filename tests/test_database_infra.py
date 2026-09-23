from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest


sys.dont_write_bytecode = True
ROOT = Path(__file__).parents[1]
PHASE_DIR = ROOT / "infra" / "next-phase"
PARAMETERS = PHASE_DIR / "environments" / "dev.database.parameters.json"
sys.path.insert(0, str(PHASE_DIR))

import database_parameters
import check_database_cleanup
import database_contract
import database_network_contract
import database_network_parameters


def load_hyphenated_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_database = load_hyphenated_module("validate_database", PHASE_DIR / "validate-database.py")
summarize_database_what_if = load_hyphenated_module(
    "summarize_database_what_if",
    PHASE_DIR / "summarize_database_what_if.py",
)


def test_database_parameters_require_free_limit_and_no_identifiers(tmp_path: Path) -> None:
    document = database_parameters.load_document(PARAMETERS)
    serialized = json.dumps(document).casefold()

    assert document["parameters"]["useFreeLimit"]["value"] is True
    assert document["parameters"]["freeLimitExhaustionBehavior"]["value"] == "AutoPause"
    assert "tenantid" not in serialized
    assert "clientid" not in serialized
    assert "principalid" not in serialized

    weakened = copy.deepcopy(document)
    weakened["parameters"]["freeLimitExhaustionBehavior"]["value"] = "BillForUsage"
    path = tmp_path / "database.parameters.json"
    path.write_text(json.dumps(weakened), encoding="utf-8")
    with pytest.raises(database_parameters.DatabaseParameterError, match="auto-pause"):
        database_parameters.load_document(path)


def test_compiled_database_policy_rejects_public_access() -> None:
    template = {
        "resources": [
            {"type": "Microsoft.Resources/resourceGroups"},
            {
                "type": "Microsoft.Resources/deployments",
                "properties": {
                    "template": {
                        "resources": [
                            {
                                "type": "Microsoft.ManagedIdentity/userAssignedIdentities",
                                "tags": "database-bootstrap-admin",
                                "properties": {"isolationScope": "Regional"},
                            },
                            {
                                "type": "Microsoft.Sql/servers",
                                "identity": {"type": "UserAssigned", "userAssignedIdentities": {"identity": {}}},
                                "properties": {
                                    "administrators": {
                                        "administratorType": "ActiveDirectory",
                                        "principalType": "Application",
                                        "azureADOnlyAuthentication": True,
                                    },
                                    "isIPv6Enabled": "Disabled",
                                    "minimalTlsVersion": "1.2",
                                    "publicNetworkAccess": "Disabled",
                                    "restrictOutboundNetworkAccess": "Enabled",
                                },
                            },
                            {
                                "type": "Microsoft.Sql/servers/databases",
                                "sku": {"name": "[format('GP_S_Gen5_{0}', parameters('maxVcores'))]", "tier": "GeneralPurpose", "family": "Gen5"},
                                "properties": {
                                    "autoPauseDelay": "[parameters('autoPauseDelayMinutes')]",
                                    "freeLimitExhaustionBehavior": "[parameters('freeLimitExhaustionBehavior')]",
                                    "highAvailabilityReplicaCount": 0,
                                    "isLedgerOn": False,
                                    "minCapacity": "[json(parameters('minVcores'))]",
                                    "readScale": "Disabled",
                                    "requestedBackupStorageRedundancy": "Local",
                                    "useFreeLimit": "[parameters('useFreeLimit')]",
                                    "zoneRedundant": False,
                                },
                            },
                        ]
                    }
                },
            },
        ],
        "metadata": "azure-sql-free-serverless auto-pause-on-exhaustion wp3-database",
    }
    assert validate_database.validate_base(template) == []

    weakened = copy.deepcopy(template)
    weakened["resources"][1]["properties"]["template"]["resources"][1]["properties"]["publicNetworkAccess"] = "Enabled"
    failures = validate_database.validate_base(weakened)
    assert any("network controls differ" in failure for failure in failures)


def test_database_network_template_is_expiring_and_private() -> None:
    source = (PHASE_DIR / "database-network.bicep").read_text(encoding="utf-8")

    assert "Microsoft.Network/privateEndpoints@" in source
    assert "Microsoft.Network/privateDnsZones@" in source
    assert "groupIds:" in source and "'sqlServer'" in source
    assert "expiresAt" in source
    assert "publicIPAddresses" not in source


def test_database_network_parameters_materialize_generated_server_outside_source(
    tmp_path: Path,
) -> None:
    config_path = PHASE_DIR / "environments" / "dev.database-network.config.json"
    config = database_network_parameters.load_config(config_path)
    assert "sqlServerName" not in config
    private_state = tmp_path / "database.state.json"
    private_state.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "resourceGroupName": config["databaseResourceGroupName"],
                "serverName": "sql-learningnemo-dev-generated",
                "databaseName": config["databaseName"],
                "sqlAdminIdentityName": "id-learningnemo-sql-admin-dev",
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "database-network.parameters.json"

    database_network_parameters.materialize(
        config_path,
        private_state,
        output,
        "2026-09-12T20:00:00Z",
    )

    values = database_network_parameters.parameter_values(output)
    assert values["sqlServerName"] == "sql-learningnemo-dev-generated"
    assert values["location"] == "eastus"
    assert values["expiresAt"] == "2026-09-12T20:00:00Z"
    assert output.stat().st_mode & 0o077 == 0


def test_database_what_if_summary_emits_types_without_names() -> None:
    database_id = (
        "/subscriptions/private/resourceGroups/private/providers/"
        "Microsoft.Sql/servers/private/databases/private"
    )
    group_id = "/subscriptions/private/resourceGroups/private"

    assert summarize_database_what_if.resource_type(database_id) == "Microsoft.Sql/servers/databases"
    assert summarize_database_what_if.resource_type(group_id) == "Microsoft.Resources/resourceGroups"
    assert summarize_database_what_if.resource_type("private-unclassified") == "unknown"
    assert summarize_database_what_if.change_resource_type(
        {
            "changeType": "Create",
            "resourceId": group_id,
            "after": {"type": "Microsoft.Authorization/roleAssignments"},
        }
    ) == "Microsoft.Authorization/roleAssignments"


def test_database_cleanup_classifies_owned_partial_state() -> None:
    assert database_contract.classify(
        [{"type": "Microsoft.ManagedIdentity/userAssignedIdentities"}]
    ) == "partial"
    assert database_contract.classify(
        [
            {"type": "Microsoft.ManagedIdentity/userAssignedIdentities"},
            {"type": "Microsoft.Network/privateEndpoints"},
        ]
    ) == "unexpected"
    assert database_contract.classify(
        [
            {"type": "Microsoft.ManagedIdentity/userAssignedIdentities", "name": "identity"},
            {"type": "Microsoft.Sql/servers", "name": "server"},
            {"type": "Microsoft.Sql/servers/databases", "name": "server/master"},
            {"type": "Microsoft.Sql/servers/databases", "name": "server/learningnemo"},
        ]
    ) == "complete"


def test_database_network_contract_includes_service_managed_interface() -> None:
    resources = [
        {"type": "Microsoft.Network/networkInterfaces"},
        {"type": "Microsoft.Network/privateDnsZones"},
        {"type": "Microsoft.Network/privateDnsZones/virtualNetworkLinks"},
        {"type": "Microsoft.Network/privateEndpoints"},
    ]

    assert database_network_contract.classify(resources) == "complete"
    assert database_network_contract.is_service_managed_network_interface(resources[0])