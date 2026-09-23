#!/usr/bin/env python3
"""Apply only the additive human-handoff schema and two exact SQL principals."""

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import UUID

from task_agent.control.mssql_client import build_connection_string, managed_identity_connect


MIGRATIONS = ("001_review_boundary.sql", "002_incident_handoff.sql", "003_execution_coordination.sql", "004_incident_initiation.sql", "005_execution_status.sql", "006_execution_reconciliation.sql", "007_readonly_analysis.sql")
GRANTS = {
    "incident": ("control.usp_list_human_incidents", "control.usp_submit_human_plan",
                 "control.usp_record_readonly_analysis", "control.usp_get_readonly_analysis", "control.usp_propose_readonly_analysis"),
    "review": ("control.usp_list_review_plans", "control.usp_decide_review_plan"),
    "execution": ("control.usp_claim_human_execution", "control.usp_record_human_execution_stage", "control.usp_complete_human_execution", "control.usp_get_human_execution", "control.usp_reconcile_human_broker"),
}
BASE_MIGRATIONS = {"001_schema.sql", "002_procedures.sql", "003_seed.sql", "004_security.sql",
                   "005_control_workflow.sql", "006_reconcile_expired_query_runs.sql"}
RETIRED_INCIDENT_GRANTS = {"control.usp_begin_human_investigation", "control.usp_record_human_investigation"}


def apply(connection, directory: Path, principals: dict) -> dict:
    if set(principals) != set(GRANTS):
        raise ValueError("exact incident/review principals required")
    if any(set(value) != {"clientId", "objectId"} for value in principals.values()):
        raise ValueError("verified client/object identity pair required")
    identifiers = {kind: UUID(value["clientId"]) for kind, value in principals.items()}
    if len(set(identifiers.values())) != 3:
        raise ValueError("separate service identities required")
    migrations = []
    for name in MIGRATIONS:
        content = (directory / name).read_text(encoding="utf-8")
        digest = hashlib.sha256(content.encode()).hexdigest()
        migrations.append((f"human-{name}", digest, tuple(batch.strip() for batch in re.split(r"(?im)^\s*GO\s*$", content) if batch.strip())))
    connection.autocommit = True
    cursor = connection.cursor()
    try:
        cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
        cursor.execute("DECLARE @result int; EXEC @result = sys.sp_getapplock @Resource=N'learningnemo-human-migrations', @LockMode=N'Exclusive', @LockOwner=N'Transaction', @LockTimeout=15000; IF @result < 0 THROW 51400, 'migration_lock_unavailable', 1;")
        cursor.execute("SELECT MigrationId, ContentHash FROM control.SchemaMigrations ORDER BY MigrationId")
        original = {row[0]: row[1] for row in cursor.fetchall()}
        if not BASE_MIGRATIONS <= set(original):
            raise ValueError("base schema migration inventory incomplete")
        for name, digest, batches in migrations:
            if name in original:
                if original[name] != digest:
                    raise ValueError("applied human migration differs; use a new version")
                continue
            for batch in batches:
                cursor.execute(batch)
            cursor.execute("INSERT control.SchemaMigrations (MigrationId, ContentHash) VALUES (?, ?)", name, digest)
        for kind, identifier in identifiers.items():
            name = f"id-learningnemo-{kind}-dev"
            sid = identifier.bytes_le
            cursor.execute("SELECT sid, type FROM sys.database_principals WHERE name = ?", name)
            rows = cursor.fetchall()
            if rows:
                old_sid = UUID(principals[kind]["objectId"]).bytes_le
                if len(rows) != 1 or bytes(rows[0][0]) not in (sid, old_sid) or rows[0][1] != "E":
                    raise ValueError("SQL principal ownership collision")
                repair_sid = bytes(rows[0][0]) != sid
                cursor.execute("SELECT COUNT(*) FROM sys.database_role_members WHERE member_principal_id = DATABASE_PRINCIPAL_ID(?)", name)
                if cursor.fetchone()[0] != 0:
                    raise ValueError("unexpected database role membership")
                cursor.execute("SELECT permission_name, state_desc, class, OBJECT_SCHEMA_NAME(major_id), OBJECT_NAME(major_id) FROM sys.database_permissions WHERE grantee_principal_id = DATABASE_PRINCIPAL_ID(?)", name)
                for permission, state, permission_class, schema, procedure in cursor.fetchall():
                    if (permission, state, permission_class) == ("CONNECT", "GRANT", 0):
                        continue
                    allowed = set(GRANTS[kind]) | (RETIRED_INCIDENT_GRANTS if kind == 'incident' else set())
                    if (permission, state, permission_class) != ("EXECUTE", "GRANT", 1) or f"{schema}.{procedure}" not in allowed:
                        raise ValueError("unexpected direct SQL permission")
                if repair_sid:
                    cursor.execute(f"DROP USER [{name}];")
                    cursor.execute(f"CREATE USER [{name}] WITH SID = 0x{sid.hex()}, TYPE = E;")
            else:
                cursor.execute(f"CREATE USER [{name}] WITH SID = 0x{sid.hex()}, TYPE = E;")
            if kind == 'incident':
                for procedure in sorted(RETIRED_INCIDENT_GRANTS):
                    cursor.execute(f"REVOKE EXECUTE ON OBJECT::{procedure} FROM [{name}];")
            for procedure in GRANTS[kind]:
                cursor.execute(f"GRANT EXECUTE ON OBJECT::{procedure} TO [{name}];")
            cursor.execute("SELECT permission_name, state_desc, class, OBJECT_SCHEMA_NAME(major_id), OBJECT_NAME(major_id) FROM sys.database_permissions WHERE grantee_principal_id = DATABASE_PRINCIPAL_ID(?)", name)
            actual = {(row[0], row[1], row[2], f"{row[3]}.{row[4]}") for row in cursor.fetchall()
                      if tuple(row[:3]) != ("CONNECT", "GRANT", 0)}
            if actual != {("EXECUTE", "GRANT", 1, procedure) for procedure in GRANTS[kind]}:
                raise ValueError("SQL permission verification failed")
            cursor.execute("SELECT sid, type FROM sys.database_principals WHERE name = ?", name)
            final_principal = cursor.fetchone()
            if bytes(final_principal[0]) != sid or final_principal[1] != "E":
                raise ValueError("SQL client identity SID verification failed")
        cursor.execute("SELECT MigrationId, ContentHash FROM control.SchemaMigrations ORDER BY MigrationId")
        final = {row[0]: row[1] for row in cursor.fetchall()}
        if any(final.get(name) != digest for name, digest in original.items()):
            raise ValueError("existing migration receipts changed")
        if any(final.get(name) != digest for name, digest, _ in migrations):
            raise ValueError("human migration receipt missing")
        cursor.execute("COMMIT TRANSACTION;")
        return {"status": "verified", "migrations": {name: digest for name, digest, _ in migrations},
                "originalReceiptsPreserved": len(original), "principals": sorted(GRANTS)}
    except Exception:
        cursor.execute("IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;")
        raise
    finally:
        cursor.close()


def main() -> None:
    expiry = datetime.fromisoformat(os.environ["LEARNINGNEMO_MIGRATION_EXPIRES_AT"].replace("Z", "+00:00"))
    if expiry.tzinfo is None or not 0 < (expiry - datetime.now(UTC)).total_seconds() <= 3600:
        raise ValueError("migration requires explicit remaining lease of at most one hour")
    client_id = os.environ["AZURE_CLIENT_ID"]
    server = os.environ["LEARNINGNEMO_SQL_SERVER"]
    connection_string = build_connection_string(server=server, database=os.environ["LEARNINGNEMO_SQL_DATABASE"],
        client_id=client_id, application_name="LearningNeMoHumanMigration")
    principals = json.loads(os.environ["LEARNINGNEMO_HUMAN_PRINCIPALS"])
    with managed_identity_connect(connection_string, client_id, timeout_seconds=120, server=server) as connection:
        result = apply(connection, Path("/app/migrations"), principals)
    from task_agent.control.execution_sql_probe import verify_rolled_back_workflow
    with managed_identity_connect(connection_string, client_id, timeout_seconds=120, server=server) as connection:
        result['executionContract'] = verify_rolled_back_workflow(connection)
    with managed_identity_connect(connection_string, client_id, timeout_seconds=120, server=server) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT MigrationId, ContentHash FROM control.SchemaMigrations")
            committed = {row[0]: row[1] for row in cursor.fetchall()}
            if any(committed.get(name) != digest for name, digest in result["migrations"].items()):
                raise ValueError("migration did not persist across connections")
            for kind, identifiers in principals.items():
                cursor.execute("SELECT sid FROM sys.database_principals WHERE name = ?", f"id-learningnemo-{kind}-dev")
                row = cursor.fetchone()
                if row is None or bytes(row[0]) != UUID(identifiers["clientId"]).bytes_le:
                    raise ValueError("SQL principal did not persist across connections")
    result["verifiedAcrossConnections"] = True
    print("PASS human_migration " + json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()