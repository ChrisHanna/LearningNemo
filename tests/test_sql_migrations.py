from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest
from sqlfluff.core import Linter


sys.dont_write_bytecode = True
ROOT = Path(__file__).parents[1]
PHASE_DIR = ROOT / "infra" / "next-phase"
sys.path.insert(0, str(PHASE_DIR))

import sql_migrations


def load_hyphenated_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_sql = load_hyphenated_module("validate_sql", PHASE_DIR / "validate-sql.py")


def identity_document() -> dict[str, dict[str, str]]:
    return {
        "control": {"name": "id-learningnemo-control-dev", "clientId": "11111111-1111-4111-8111-111111111111"},
        "diagnostic": {"name": "id-learningnemo-diagnostic-dev", "clientId": "22222222-2222-4222-8222-222222222222"},
        "remediation": {"name": "id-learningnemo-remediation-dev", "clientId": "33333333-3333-4333-8333-333333333333"},
        "verifier": {"name": "id-learningnemo-verifier-dev", "clientId": "44444444-4444-4444-8444-444444444444"},
        "queryRunner": {"name": "id-learningnemo-query-runner-dev", "clientId": "55555555-5555-4555-8555-555555555555"},
    }


def migration_sources() -> tuple[str, str, str, str, str, str]:
    sql_dir = PHASE_DIR / "sql"
    return tuple(
        (sql_dir / name).read_text(encoding="utf-8")
        for name in (
            "001_schema.sql",
            "002_procedures.sql",
            "003_seed.sql",
            "004_security.sql.tmpl",
            "005_control_workflow.sql",
            "006_reconcile_expired_query_runs.sql",
        )
    )


def test_sql_static_policy_accepts_reviewed_migrations() -> None:
    assert validate_sql.validate_sql(*migration_sources()) == []


def test_sql_policy_rejects_write_in_unsafe_query() -> None:
    schema, procedures, seed, security, control_workflow, recovery = migration_sources()
    procedures = procedures.replace(
        "SELECT COUNT_BIG(*) AS unreachable_count",
        "UPDATE lab.HierarchyEdges SET ChildNode = ChildNode; SELECT COUNT_BIG(*) AS unreachable_count",
    )

    failures = validate_sql.validate_sql(
        schema,
        procedures,
        seed,
        security,
        control_workflow,
        recovery,
    )

    assert any("not read-only" in failure for failure in failures)


def test_migration_bundle_materializes_distinct_identity_sids(tmp_path: Path) -> None:
    identities = tmp_path / "identities.json"
    output = tmp_path / "migrations.sql"
    identities.write_text(json.dumps(identity_document()), encoding="utf-8")

    sql_migrations.materialize(identities, output)

    bundle = output.read_text(encoding="utf-8")
    assert "__CONTROL" not in bundle
    assert "id-learningnemo-control-dev" in bundle
    assert sql_migrations.sid_hex("11111111-1111-4111-8111-111111111111") in bundle
    assert bundle.count("BEGIN MIGRATION") == 6
    assert bundle.count("migration_hash_mismatch") == 6
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    linter = Linter(dialect="tsql")
    violations = [
        str(violation)
        for index, batch in enumerate(bundle.split("\nGO\n"), start=1)
        if batch.strip()
        for violation in linter.parse_string(batch, fname=f"migration-batch-{index}.sql").violations
    ]
    assert violations == []


def test_migration_bundle_rejects_duplicate_or_invalid_identity(tmp_path: Path) -> None:
    identities = identity_document()
    identities["verifier"]["clientId"] = identities["control"]["clientId"]
    path = tmp_path / "identities.json"
    path.write_text(json.dumps(identities), encoding="utf-8")

    with pytest.raises(sql_migrations.MigrationError, match="distinct"):
        sql_migrations.load_identities(path)