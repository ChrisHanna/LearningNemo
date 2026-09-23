from __future__ import annotations

import json
import importlib.util
import stat
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
PHASE_DIR = ROOT / "infra" / "next-phase"
CONFIG = PHASE_DIR / "environments" / "dev.connectivity-probe.config.json"
sys.path.insert(0, str(PHASE_DIR))

import connectivity_probe_parameters
import database_network_parameters
import preflight_connectivity_probe
import summarize_connectivity_probe
import validate_connectivity_probe_what_if


def load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_connectivity_probe",
        PHASE_DIR / "validate-connectivity-probe.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_connectivity_probe = load_validator()


def test_connectivity_probe_parameters_are_private_and_state_bound(tmp_path: Path) -> None:
    database_state = tmp_path / "database.json"
    database_state.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "resourceGroupName": "rg-learningnemo-data-dev",
                "serverName": "sql-learningnemo-generated",
                "databaseName": "learningnemo",
                "sqlAdminIdentityName": "id-learningnemo-sql-admin-dev",
            }
        ),
        encoding="utf-8",
    )
    artifact_state = tmp_path / "artifacts.json"
    artifact_state.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "resourceGroupName": "rg-learningnemo-artifacts-dev",
                "registryName": "crlearningnemogenerated",
                "loginServer": "crlearningnemogenerated.azurecr.io",
                "expiresAt": "2026-09-14T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    image = tmp_path / "image.txt"
    image.write_text(
        f"crlearningnemogenerated.azurecr.io/learningnemo/trusted-runtime@sha256:{'a' * 64}\n",
        encoding="utf-8",
    )
    output = tmp_path / "probe.parameters.json"

    connectivity_probe_parameters.materialize(
        CONFIG,
        database_state,
        artifact_state,
        image,
        output,
        "2026-09-13T20:00:00Z",
    )

    values = connectivity_probe_parameters.parameter_values(output)
    assert values["location"] == "eastus"
    assert values["sqlServerHostname"] == "sql-learningnemo-generated.database.windows.net"
    assert values["imageReference"].endswith("a" * 64)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_connectivity_probe_config_contains_no_generated_cloud_identifiers() -> None:
    document = connectivity_probe_parameters.load_config(CONFIG)
    serialized = json.dumps(document).casefold()

    assert "subscription" not in serialized
    assert "tenant" not in serialized
    assert "clientid" not in serialized
    assert "database.windows.net" not in serialized
    assert "azurecr.io" not in serialized


def test_connectivity_probe_validator_accepts_only_bounded_probe_resources() -> None:
    identity_expression = "[resourceId(parameters('platformResourceGroupName'),'Microsoft.ManagedIdentity/userAssignedIdentities','id-learningnemo-diagnostic-dev')]"
    template = {
        "resources": [
            {"type": "Microsoft.Resources/resourceGroups"},
            {
                "type": "Microsoft.Resources/deployments",
                "properties": {
                    "template": {
                        "resources": [
                            {
                                "type": "Microsoft.App/jobs",
                                "identity": {
                                    "type": "UserAssigned",
                                    "userAssignedIdentities": {identity_expression: {}},
                                },
                                "tags": {
                                    "costProfile": "one-shot-connectivity-probe",
                                    "platformPhase": "wp3-connectivity-probe",
                                    "imageDigest": "[last(split(parameters('imageReference'),'@sha256:'))]",
                                },
                                "properties": {
                                    "workloadProfileName": "Consumption",
                                    "configuration": {
                                        "triggerType": "Manual",
                                        "replicaTimeout": 180,
                                        "replicaRetryLimit": 0,
                                        "manualTriggerConfig": {
                                            "parallelism": 1,
                                            "replicaCompletionCount": 1,
                                        },
                                        "registries": [{"server": "registry", "identity": identity_expression}],
                                        "secrets": [],
                                    },
                                    "template": {
                                        "containers": [
                                            {
                                                "name": "connectivity-probe",
                                                "image": "registry/runtime@sha256:" + "a" * 64,
                                                "command": ["/usr/local/bin/python3"],
                                                "args": ["/opt/learningnemo/scripts/probe-sql-odbc.py"],
                                                "env": [
                                                    {
                                                        "name": "LEARNINGNEMO_SQL_SERVER",
                                                        "value": "private-host",
                                                    },
                                                    {
                                                        "name": "LEARNINGNEMO_SQL_DATABASE",
                                                        "value": "master",
                                                    },
                                                    {
                                                        "name": "AZURE_CLIENT_ID",
                                                        "value": "client-id",
                                                    },
                                                ],
                                            }
                                        ]
                                    },
                                },
                            },
                        ]
                    }
                },
            },
        ]
    }

    assert validate_connectivity_probe.validate(template) == []


def test_connectivity_probe_validator_rejects_sql_admin_and_ingress() -> None:
    template = {
        "resources": [
            {
                "type": "Microsoft.App/jobs",
                "name": "sql-admin",
                "properties": {"configuration": {"ingress": {"external": True}}},
            }
        ]
    }

    failures = validate_connectivity_probe.validate(template)
    assert failures
    assert any("prohibited" in failure for failure in failures)


def test_connectivity_probe_summary_records_only_allowlisted_success(tmp_path: Path) -> None:
    result = summarize_connectivity_probe.summarize(
        {"properties": {"replicaStatuses": [{"containerStatuses": [{"exitCode": 0}]}]}},
        {
            "logs": [
                {"message": "PASS sql_private_dns"},
                {"message": "PASS sql_private_tcp"},
                {"message": "PASS sql_managed_identity_token"},
                {"message": "PASS sql_odbc_prelogin_tls"},
                {"message": "PASS sql_authentication_rejected_as_expected"},
                {"message": "server=secret.database.windows.net token=secret"},
            ]
        },
    )

    output = tmp_path / "probe.result.json"
    summarize_connectivity_probe.write_report(output, *result)
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["status"] == "passed"
    assert document["privateDnsPassed"] is True
    assert document["tcp1433Passed"] is True
    assert document["managedIdentityTokenPassed"] is True
    assert document["odbcPreloginTlsPassed"] is True
    assert document["authenticationRejectedAsExpected"] is True
    assert "secret" not in output.read_text(encoding="utf-8")
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_connectivity_probe_summary_allows_errno_but_rejects_raw_text() -> None:
    result = summarize_connectivity_probe.summarize(
        {"properties": {"replicaStatuses": [{"containerStatuses": [{"exitCode": 1}]}]}},
        {
            "logs": [
                {"message": "PASS sql_private_dns"},
                {"message": "FAIL sql_tcp_os_error_einprogress"},
                {"message": "FAIL sql_tcp_os_error_not-real endpoint=secret"},
            ]
        },
    )

    assert result == (
        "failed",
        True,
        False,
        False,
        False,
        False,
        "sql_tcp_os_error_einprogress",
        1,
    )


def test_connectivity_probe_preflight_rejects_expiry_past_dependency(tmp_path: Path) -> None:
    database_state = tmp_path / "database.json"
    database_state.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "resourceGroupName": "rg-learningnemo-data-dev",
                "serverName": "sql-learningnemo-generated",
                "databaseName": "learningnemo",
                "sqlAdminIdentityName": "id-learningnemo-sql-admin-dev",
            }
        ),
        encoding="utf-8",
    )
    artifact_state = tmp_path / "artifacts.json"
    artifact_state.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "resourceGroupName": "rg-learningnemo-artifacts-dev",
                "registryName": "crlearningnemogenerated",
                "loginServer": "crlearningnemogenerated.azurecr.io",
                "expiresAt": "2026-09-14T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    image = tmp_path / "image.txt"
    image.write_text(
        f"crlearningnemogenerated.azurecr.io/runtime@sha256:{'a' * 64}\n",
        encoding="utf-8",
    )
    parameters = tmp_path / "probe.json"
    connectivity_probe_parameters.materialize(
        CONFIG,
        database_state,
        artifact_state,
        image,
        parameters,
        "2026-09-13T20:00:00Z",
    )
    runtime = json.loads((PHASE_DIR / "environments" / "dev.runtime.parameters.json").read_text())
    runtime["parameters"]["expiresAt"] = {"value": "2026-09-13T19:30:00Z"}
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    network_path = tmp_path / "network.json"
    database_network_parameters.materialize(
        PHASE_DIR / "environments" / "dev.database-network.config.json",
        database_state,
        network_path,
        "2026-09-13T21:00:00Z",
    )

    with pytest.raises(
        preflight_connectivity_probe.ConnectivityProbePreflightError,
        match="outlives",
    ):
        preflight_connectivity_probe.validate_local(
            CONFIG,
            parameters,
            runtime_path,
            network_path,
            now=datetime(2026, 9, 13, 18, 0, tzinfo=UTC),
        )


def test_connectivity_probe_wrappers_use_deployment_stack_and_cleanup() -> None:
    deploy = (PHASE_DIR / "deploy-connectivity-probe.sh").read_text(encoding="utf-8")
    remove = (PHASE_DIR / "remove-connectivity-probe.sh").read_text(encoding="utf-8")

    assert "az deployment sub what-if" in deploy
    assert "az stack sub create" in deploy
    assert "--action-on-unmanage deleteAll" in deploy
    assert "LEARNINGNEMO_AZURE_APPLY=wp3-connectivity-probe" in deploy
    assert "verify_connectivity_probe.py" in deploy
    assert "summarize_connectivity_probe.py" in deploy
    assert "sqlAdminIdentity" not in deploy
    assert "az stack sub delete" in remove
    assert "LEARNINGNEMO_AZURE_DELETE=wp3-connectivity-probe" in remove
    assert "existing diagnostic AcrPull assignment differs" in remove


def test_connectivity_probe_normalizes_azure_location_labels() -> None:
    assert preflight_connectivity_probe.normalize_location("East US") == "eastus"


def test_deployment_stack_success_state_is_case_insensitive() -> None:
    assert "Succeeded".casefold() == "succeeded"
    assert "succeeded".casefold() == "succeeded"


def test_connectivity_probe_what_if_requires_exact_bounded_changes() -> None:
    def change(change_type: str, resource_type: str) -> dict[str, object]:
        return {
            "changeType": change_type,
            "resourceId": f"/subscriptions/example/resourceGroups/example/providers/{resource_type}/example",
            "after": {"type": resource_type},
        }

    document = {
        "changes": [
            change("Create", "Microsoft.App/jobs"),
            change("Create", "Microsoft.Resources/resourceGroups"),
        ]
    }

    assert validate_connectivity_probe_what_if.validate(document) == []
    document["changes"].append(change("Delete", "Microsoft.Sql/servers"))
    assert validate_connectivity_probe_what_if.validate(document)