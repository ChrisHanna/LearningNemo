from __future__ import annotations

import hashlib
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
CONFIG = PHASE_DIR / "environments" / "dev.migration.config.json"
sys.path.insert(0, str(PHASE_DIR))

import migration_parameters
import migration_identity_parameters
import sql_migrations
import summarize_migration_failure
import summarize_sql_admin_probe
import wait_migration_execution
import verify_migration_evidence


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_migration", PHASE_DIR / "validate-migration.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_migration = load_validator()


def identity_document() -> dict[str, dict[str, str]]:
    return {
        "control": {"name": "id-learningnemo-control-dev", "clientId": "11111111-1111-4111-8111-111111111111"},
        "diagnostic": {"name": "id-learningnemo-diagnostic-dev", "clientId": "22222222-2222-4222-8222-222222222222"},
        "remediation": {"name": "id-learningnemo-remediation-dev", "clientId": "33333333-3333-4333-8333-333333333333"},
        "verifier": {"name": "id-learningnemo-verifier-dev", "clientId": "44444444-4444-4444-8444-444444444444"},
        "queryRunner": {"name": "id-learningnemo-query-runner-dev", "clientId": "55555555-5555-4555-8555-555555555555"},
    }


def test_migration_parameters_bind_private_state_image_and_bundle(tmp_path: Path) -> None:
    identities = tmp_path / "identities.json"
    identities.write_text(json.dumps(identity_document()), encoding="utf-8")
    bundle = tmp_path / "migrations.sql"
    sql_migrations.materialize(identities, bundle)
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
        )
    )
    artifact_state = tmp_path / "artifacts.json"
    artifact_state.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "resourceGroupName": "rg-learningnemo-artifacts-dev",
                "registryName": "crlearningnemogenerated",
                "loginServer": "crlearningnemogenerated.azurecr.io",
                "expiresAt": "2026-09-12T20:00:00Z",
            }
        )
    )
    image = tmp_path / "image.txt"
    image.write_text(f"crlearningnemogenerated.azurecr.io/learningnemo/trusted-runtime@sha256:{'a' * 64}\n")
    output = tmp_path / "migration.parameters.json"

    migration_parameters.materialize(
        CONFIG,
        database_state,
        artifact_state,
        image,
        bundle,
        output,
        "2026-09-12T20:00:00Z",
    )

    values = migration_parameters.parameter_values(output)
    assert values["migrationBundleSha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert values["sqlServerHostname"].endswith(".database.windows.net")
    assert values["imageReference"].endswith("a" * 64)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_migration_identity_overlay_is_bounded_and_owner_only(tmp_path: Path) -> None:
    output = tmp_path / "migration-identity.parameters.json"
    migration_identity_parameters.materialize(
        PHASE_DIR / "environments" / "dev.database.parameters.json",
        output,
        "2026-09-12T20:00:00Z",
    )

    values = migration_identity_parameters.parameter_values(output)
    assert values["location"] == "centralus"
    assert values["expiresAt"] == "2026-09-12T20:00:00Z"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_migration_waiter_stops_on_success_or_failure() -> None:
    statuses = iter(["Running", "Running", "Succeeded"])
    ticks = iter([0.0, 1.0, 2.0])

    status = wait_migration_execution.wait_for_execution(
        lambda: next(statuses),
        timeout_seconds=10,
        interval_seconds=2,
        monotonic=lambda: next(ticks),
        sleep=lambda _seconds: None,
    )

    assert status == "Succeeded"
    with pytest.raises(wait_migration_execution.MigrationWaitError, match="terminal status"):
        wait_migration_execution.wait_for_execution(
            lambda: "Failed",
            timeout_seconds=10,
            interval_seconds=2,
        )


def test_migration_failure_summary_allows_only_reason_and_exit_code() -> None:
    execution = {
        "properties": {
            "replicaStatuses": [
                {"name": "sensitive-replica", "containerStatuses": [{"exitCode": 29}]},
            ]
        }
    }
    logs = {
        "logs": [
            {"message": "FAIL sql_tcp_connect_failed"},
            {"message": "server=secret.database.windows.net token=secret"},
        ]
    }

    assert summarize_migration_failure.summarize(execution, logs) == (
        "sql_tcp_connect_failed",
        29,
    )


def test_migration_failure_summary_allows_errno_name_but_not_raw_text() -> None:
    logs = {
        "logs": [
            {"message": "FAIL sql_tcp_os_error_einprogress"},
            {"message": "FAIL sql_tcp_os_error_not-a-real-errno server=secret"},
        ]
    }

    assert summarize_migration_failure.summarize({}, logs) == (
        "sql_tcp_os_error_einprogress",
        None,
    )


def test_migration_failure_report_is_owner_only_and_identifier_free(tmp_path: Path) -> None:
    output = tmp_path / "migration.failure.json"

    summarize_migration_failure.write_report(
        output,
        "sql_authentication_failed",
        22,
        recorded_at=datetime(2026, 9, 13, 14, 55, tzinfo=UTC),
    )

    assert json.loads(output.read_text(encoding="utf-8")) == {
        "schemaVersion": 1,
        "status": "failed",
        "failureCategory": "sql_authentication_failed",
        "processExitCode": 22,
        "recordedAt": "2026-09-13T14:55:00Z",
    }
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_sql_admin_probe_accepts_only_authorized_target_login_evidence() -> None:
    execution = {"properties": {"status": "Failed"}}
    logs = {
        "logs": [
            {"message": "PASS sql_private_dns"},
            {"message": "PASS sql_private_tcp"},
            {"message": "PASS sql_managed_identity_token"},
            {"message": "FAIL sql_unexpected_authorization"},
        ]
    }

    summarize_sql_admin_probe.summarize(execution, logs)
    logs["logs"][-1]["message"] = "FAIL sql_odbc_prelogin_failed"
    with pytest.raises(summarize_sql_admin_probe.SqlAdminProbeSummaryError, match="closed evidence"):
        summarize_sql_admin_probe.summarize(execution, logs)


def test_sql_admin_probe_loader_accepts_plain_cli_log_text(tmp_path: Path) -> None:
    logs = tmp_path / "logs.txt"
    logs.write_text("PASS sql_private_dns\nFAIL sql_unexpected_authorization\n", encoding="utf-8")

    assert summarize_sql_admin_probe.load_json(logs).startswith("PASS sql_private_dns")


def test_sql_admin_probe_observations_are_allowlisted() -> None:
    status, results = summarize_sql_admin_probe.observations(
        {"properties": {"status": "Failed"}},
        "PASS sql_private_dns\nFAIL sql_odbc_unknown_failed\nFAIL sensitive_value\n",
    )

    assert status == "Failed"
    assert results == {
        ("PASS", "sql_private_dns"),
        ("FAIL", "sql_odbc_unknown_failed"),
    }


def test_permanent_migration_evidence_binds_six_receipts_and_image(tmp_path: Path) -> None:
    image = tmp_path / "image.txt"
    image.write_text(f"example.azurecr.io/learningnemo/runtime@sha256:{'a' * 64}\n")
    release = tmp_path / "release.json"
    release.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "imageReference": image.read_text().strip(),
                "sourceRevision": "b" * 64,
                "builtAt": "2026-01-01T00:00:00Z",
                "sbomSha256": "c" * 64,
                "vulnerabilityScanSha256": "d" * 64,
                "signatureVerificationSha256": "e" * 64,
                "signatureVerified": True,
                "criticalVulnerabilities": 0,
                "highVulnerabilities": 0,
                "approvalAuthority": "azure-sql",
                "approvalSchemaSha256": "f" * 64,
            }
        )
    )
    manifest = tmp_path / "migration.manifest.json"
    document = {
        "schemaVersion": 1,
        "appliedAt": "2026-09-12T12:00:00+00:00",
        "configuration": migration_parameters.load_config(CONFIG),
        "migrationBundleSha256": "1" * 64,
        "imageDigest": "a" * 64,
        "sourceRevision": "b" * 64,
        "executionStatus": "Succeeded",
        "receiptCount": 6,
        "evidenceSha256": {
            "compiledTemplate": "2" * 64,
            "privateParameters": "3" * 64,
            "releaseAttestation": "4" * 64,
            "executionResult": "5" * 64,
        },
    }
    manifest.write_text(json.dumps(document))

    verify_migration_evidence.verify(manifest, CONFIG, release, image)

    document["receiptCount"] = 3
    manifest.write_text(json.dumps(document))
    with pytest.raises(verify_migration_evidence.MigrationEvidenceError, match="receipt"):
        verify_migration_evidence.verify(manifest, CONFIG, release, image)