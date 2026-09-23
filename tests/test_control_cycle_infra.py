from __future__ import annotations

import importlib.util
import json
import stat
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
PHASE_DIR = ROOT / "infra" / "next-phase"
CONFIG = PHASE_DIR / "environments" / "dev.control-cycle.config.json"
sys.path.insert(0, str(PHASE_DIR))

import control_cycle_parameters
import summarize_control_cycle
import validate_control_cycle_what_if
import verify_control_cycle_evidence


def load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_control_cycle",
        PHASE_DIR / "validate-control-cycle.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_control_cycle = load_validator()


def test_control_cycle_parameters_hash_subjects_and_are_owner_only(tmp_path: Path) -> None:
    approver_subject = "portfolio-approver@example.invalid"
    control_principal = "66666666-6666-4666-8666-666666666666"
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {
                "tenantId": "77777777-7777-4777-8777-777777777777",
                "controlCallerApplicationId": "88888888-8888-4888-8888-888888888888",
                "controlCallerPrincipalId": control_principal,
                "serviceApplicationIds": {
                    "diagnostic": "11111111-1111-4111-8111-111111111111",
                    "queryRunner": "22222222-2222-4222-8222-222222222222",
                    "remediation": "33333333-3333-4333-8333-333333333333",
                    "verifier": "44444444-4444-4444-8444-444444444444",
                },
            }
        ),
        encoding="utf-8",
    )
    database = tmp_path / "database.json"
    database.write_text(
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
    artifacts = tmp_path / "artifacts.json"
    artifacts.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "resourceGroupName": "rg-learningnemo-artifacts-dev",
                "registryName": "crlearningnemogenerated",
                "loginServer": "crlearningnemogenerated.azurecr.io",
                "expiresAt": "2026-09-14T04:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    image = tmp_path / "image.txt"
    image.write_text(
        f"crlearningnemogenerated.azurecr.io/learningnemo/trusted-runtime@sha256:{'a' * 64}\n",
        encoding="utf-8",
    )
    output = tmp_path / "control.parameters.json"

    control_cycle_parameters.materialize(
        CONFIG,
        auth,
        database,
        artifacts,
        image,
        output,
        "2026-09-14T03:00:00Z",
        approver_subject=approver_subject,
    )

    values = control_cycle_parameters.parameter_values(output)
    serialized = output.read_text(encoding="utf-8")
    assert values["approverHash"] == control_cycle_parameters.subject_hash(
        "azure-cli-approver", approver_subject
    )
    assert values["operatorHash"] == control_cycle_parameters.subject_hash(
        "managed-identity-operator", control_principal
    )
    assert values["approverHash"] != values["operatorHash"]
    assert approver_subject not in serialized
    assert control_principal not in serialized
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_control_cycle_source_config_contains_no_generated_identifiers() -> None:
    serialized = json.dumps(control_cycle_parameters.load_config(CONFIG)).casefold()

    assert "subscription" not in serialized
    assert "clientid" not in serialized
    assert "database.windows.net" not in serialized
    assert "azurecr.io" not in serialized


def test_control_cycle_validator_accepts_split_identity_job() -> None:
    control_identity = "[resourceId('control')]"
    diagnostic_identity = "[resourceId('diagnostic')]"
    template = {
        "metadata": {
            "contract": "one-shot-control-cycle wp3-control-cycle @sha256: "
            "-control- -diagnostic- run-live-cycle-workflow.py"
        },
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
                                    "userAssignedIdentities": {
                                        control_identity: {},
                                        diagnostic_identity: {},
                                    },
                                },
                                "properties": {
                                    "workloadProfileName": "Consumption",
                                    "configuration": {
                                        "triggerType": "Manual",
                                        "replicaTimeout": 600,
                                        "replicaRetryLimit": 0,
                                        "manualTriggerConfig": {
                                            "parallelism": 1,
                                            "replicaCompletionCount": 1,
                                        },
                                        "identitySettings": [
                                            {"identity": control_identity, "lifecycle": "Main"}
                                        ],
                                        "registries": [
                                            {"server": "registry", "identity": diagnostic_identity}
                                        ],
                                        "secrets": [],
                                    },
                                    "template": {
                                        "containers": [
                                            {
                                                "image": "registry/runtime@sha256:" + "a" * 64,
                                                "command": ["/usr/local/bin/python3"],
                                                "args": [
                                                    "/opt/learningnemo/scripts/run-live-cycle-workflow.py"
                                                ],
                                                "env": [
                                                    {"name": name, "value": "bound"}
                                                    for name in (
                                                        "LEARNINGNEMO_WORKER_ENDPOINTS",
                                                        "LEARNINGNEMO_WORKER_AUDIENCES",
                                                        "LEARNINGNEMO_SQL_SERVER",
                                                        "LEARNINGNEMO_SQL_DATABASE",
                                                        "AZURE_CLIENT_ID",
                                                        "LEARNINGNEMO_APPROVER_HASH",
                                                        "LEARNINGNEMO_OPERATOR_HASH",
                                                    )
                                                ],
                                            }
                                        ]
                                    },
                                },
                            }
                        ]
                    }
                },
            },
        ],
    }

    assert validate_control_cycle.validate(template) == []


def test_control_cycle_validator_rejects_rbac_secrets_and_ingress() -> None:
    template = {
        "metadata": {
            "contract": "one-shot-control-cycle wp3-control-cycle @sha256: "
            "-control- -diagnostic- run-live-cycle-workflow.py"
        },
        "resources": [
            {"type": "Microsoft.Authorization/roleAssignments"},
            {
                "type": "Microsoft.App/jobs",
                "identity": {"type": "SystemAssigned"},
                "properties": {
                    "configuration": {
                        "ingress": {"external": True},
                        "secrets": [{"name": "credential"}],
                    }
                },
            },
        ],
    }

    failures = validate_control_cycle.validate(template)
    assert failures
    assert any("prohibited" in failure for failure in failures)


def test_control_cycle_summary_requires_all_eight_success_stages() -> None:
    logs = {
        "logs": [
            {"message": f"PASS {stage}"}
            for stage in sorted(summarize_control_cycle.EXPECTED_STAGES)
        ]
        + [{"message": "endpoint=secret token=secret"}]
    }

    checks = summarize_control_cycle.summarize(
        {"properties": {"status": "Succeeded"}},
        logs,
    )
    assert checks == {stage: True for stage in sorted(summarize_control_cycle.EXPECTED_STAGES)}
    logs["logs"][0]["message"] = "FAIL live_workflow_failed"
    with pytest.raises(summarize_control_cycle.ControlCycleSummaryError, match="contract"):
        summarize_control_cycle.summarize({"properties": {"status": "Failed"}}, logs)


def test_control_cycle_observations_expose_only_allowlisted_categories() -> None:
    status, passes, failures = summarize_control_cycle.observations(
        {"properties": {"status": "Failed"}},
        "PASS live_workflow_workers_ready\n"
        "FAIL workflow_initialize_failed\n"
        "FAIL worker_http_503\n"
        "FAIL sensitive_internal_detail\n",
    )

    assert status == "Failed"
    assert passes == {"live_workflow_workers_ready"}
    assert failures == {"workflow_initialize_failed", "worker_http_503"}


def test_control_cycle_report_binds_release_template_and_execution(tmp_path: Path) -> None:
    image_reference = f"example.azurecr.io/learningnemo/runtime@sha256:{'a' * 64}"
    image = tmp_path / "image.txt"
    image.write_text(image_reference + "\n", encoding="utf-8")
    release = tmp_path / "release.json"
    release.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "imageReference": image_reference,
                "sourceRevision": "b" * 64,
                "builtAt": "2026-09-13T00:00:00Z",
                "sbomSha256": "c" * 64,
                "vulnerabilityScanSha256": "d" * 64,
                "signatureVerificationSha256": "e" * 64,
                "signatureVerified": True,
                "criticalVulnerabilities": 0,
                "highVulnerabilities": 0,
                "approvalAuthority": "azure-sql",
                "approvalSchemaSha256": "f" * 64,
            }
        ),
        encoding="utf-8",
    )
    template = tmp_path / "template.json"
    parameters = tmp_path / "parameters.json"
    execution = tmp_path / "execution.json"
    for path in (template, parameters, execution):
        path.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "result.json"

    summarize_control_cycle.write_report(
        output,
        {stage: True for stage in sorted(summarize_control_cycle.EXPECTED_STAGES)},
        release,
        image,
        template,
        parameters,
        execution,
        recorded_at=datetime(2026, 9, 13, 12, 0, tzinfo=UTC),
    )

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["status"] == "passed"
    assert document["imageDigest"] == "a" * 64
    assert set(document["evidenceSha256"]) == {
        "compiledTemplate",
        "privateParameters",
        "releaseAttestation",
        "executionResult",
    }
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    verify_control_cycle_evidence.verify(output, release, image)

    document["checks"].pop(next(iter(document["checks"])))
    output.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(verify_control_cycle_evidence.ControlCycleEvidenceError, match="stage"):
        verify_control_cycle_evidence.verify(output, release, image)


def test_control_cycle_what_if_allows_only_group_and_job_creates() -> None:
    def change(change_type: str, resource_type: str) -> dict[str, object]:
        return {
            "changeType": change_type,
            "resourceId": f"/subscriptions/example/providers/{resource_type}/example",
            "after": {"type": resource_type},
        }

    document = {
        "changes": [
            change("Create", "Microsoft.App/jobs"),
            change("Create", "Microsoft.Resources/resourceGroups"),
        ]
    }
    assert validate_control_cycle_what_if.validate(document) == []
    document["changes"].append(change("Modify", "Microsoft.Authorization/roleAssignments"))
    assert validate_control_cycle_what_if.validate(document)


def test_control_cycle_wrappers_enforce_stack_cleanup_and_grant_separation() -> None:
    deploy = (PHASE_DIR / "deploy-control-cycle.sh").read_text(encoding="utf-8")
    remove = (PHASE_DIR / "remove-control-cycle.sh").read_text(encoding="utf-8")

    assert "az deployment sub what-if" in deploy
    assert "az stack sub create" in deploy
    assert "LEARNINGNEMO_AZURE_APPLY=wp3-control-cycle" in deploy
    assert "verify_control_cycle.py" in deploy
    assert "summarize_control_cycle.py" in deploy
    assert "verify_migration_evidence.py" in deploy
    assert "bounded = min(requested" in deploy
    assert "az stack sub delete" in remove
    assert "LEARNINGNEMO_AZURE_DELETE=wp3-control-cycle" in remove
    assert 'control_assignments" != "0"' in remove
    assert 'diagnostic_assignments" != "1"' in remove


def test_control_cycle_job_name_meets_container_apps_limit() -> None:
    source = (PHASE_DIR / "control-cycle-job.bicep").read_text(encoding="utf-8")
    deployed_name = "caj-learningnemo-cycle-dev"

    assert "caj-${projectName}-cycle-${environment}" in source
    assert len(deployed_name) <= 32