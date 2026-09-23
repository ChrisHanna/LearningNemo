#!/usr/bin/env python3
"""Validate the static Azure SQL migration and least-authority contract."""

from __future__ import annotations

import re
import sys
from pathlib import Path


SQL_DIR = Path(__file__).with_name("sql")
REQUIRED_TABLES = {
    "control.Tasks",
    "control.TaskEvents",
    "control.Engagements",
    "control.AgentRegistrations",
    "control.ResolutionPlans",
    "control.Approvals",
    "control.Executions",
    "control.Verifications",
    "control.EvidenceEvents",
    "lab.HierarchyEdges",
    "lab.QueryRuns",
    "lab.QueryVersions",
}
REQUIRED_PROCEDURES = {
    "control.usp_issue_approval",
    "ops.usp_get_diagnostic_snapshot",
    "ops.usp_register_query_run",
    "ops.usp_mark_query_running",
    "ops.usp_request_owned_query_cancel",
    "ops.usp_confirm_owned_query_cancelled",
    "ops.usp_mark_query_failed",
    "ops.usp_expire_query_leases",
    "ops.usp_run_controlled_unsafe_query",
    "ops.usp_run_cycle_safe_query",
    "ops.usp_activate_cycle_safe_query",
    "ops.usp_verify_cycle_recovery",
    "control.usp_initialize_cycle_demo",
    "control.usp_record_cycle_containment_plan",
    "control.usp_record_cycle_execution",
    "control.usp_record_cycle_verification",
    "control.usp_get_cycle_demo_summary",
}
REQUIRED_ROLES = {
    "learningnemo_control",
    "learningnemo_diagnostic",
    "learningnemo_remediator",
    "learningnemo_verifier",
    "learningnemo_query_runner",
}


def procedure_body(sql: str, name: str) -> str:
    match = re.search(
        rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+{re.escape(name)}\b(.*?)(?:\nGO\s*(?:\n|$))",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ""


def validate_sql(
    schema: str,
    procedures: str,
    seed: str,
    security: str,
    control_workflow: str,
    recovery: str,
) -> list[str]:
    failures: list[str] = []
    combined = "\n".join((schema, procedures, seed, security, control_workflow, recovery))
    tables = set(re.findall(r"CREATE\s+TABLE\s+((?:control|lab)\.[A-Za-z0-9_]+)", schema, re.IGNORECASE))
    if {name.casefold() for name in tables} != {name.casefold() for name in REQUIRED_TABLES | {"control.SchemaMigrations"}}:
        failures.append("SQL table inventory differs")
    procedures_found = set(
        re.findall(
            r"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+((?:control|ops)\.[A-Za-z0-9_]+)",
            procedures + "\n" + control_workflow + "\n" + recovery,
            re.IGNORECASE,
        )
    )
    if {name.casefold() for name in procedures_found} != {name.casefold() for name in REQUIRED_PROCEDURES}:
        failures.append("SQL procedure inventory differs")
    for table in (
        "Tasks",
        "Engagements",
        "AgentRegistrations",
        "ResolutionPlans",
        "Approvals",
        "Executions",
        "Verifications",
        "QueryRuns",
    ):
        block = re.search(
            rf"CREATE\s+TABLE\s+(?:control|lab)\.{table}\s*\((.*?)\n\s*\);",
            schema,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if block is None or "rowversion" not in block.group(1).casefold():
            failures.append(f"mutable SQL table lacks rowversion: {table}")

    unsafe = procedure_body(procedures, "ops.usp_run_controlled_unsafe_query")
    safe = procedure_body(procedures, "ops.usp_run_cycle_safe_query")
    remediation = procedure_body(procedures, "ops.usp_activate_cycle_safe_query")
    if "MAXRECURSION 0" not in unsafe or "MAXDOP 1" not in unsafe:
        failures.append("controlled unsafe query lacks required recursion and DOP bounds")
    unsafe_statements = unsafe.split("AS\nBEGIN", 1)[-1]
    if re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|KILL)\b", unsafe_statements, re.IGNORECASE):
        failures.append("controlled unsafe query is not read-only")
    if combined.upper().count("MAXRECURSION 0") != 1:
        failures.append("MAXRECURSION 0 must appear only in the controlled unsafe procedure")
    if "MAXRECURSION 16" not in safe or "CHARINDEX" not in safe or "depth < 16" not in safe:
        failures.append("cycle-safe query lacks finite recursion and path detection")
    for required in (
        "SET XACT_ABORT ON",
        "BEGIN TRANSACTION",
        "UPDLOCK, HOLDLOCK",
        "ReceiptHash = @receipt_hash",
        "OneTimeId = @one_time_id",
        "ExpiresAt > @now_utc",
        "UPDATE lab.QueryVersions",
        "UPDATE control.Approvals",
        "UPDATE control.ResolutionPlans",
        "UPDATE control.Tasks",
        "COMMIT TRANSACTION",
        "ROLLBACK TRANSACTION",
    ):
        if required.casefold() not in remediation.casefold():
            failures.append(f"atomic remediation procedure is missing: {required}")

    if not {"(N'A', N'B')", "(N'B', N'C')", "(N'C', N'A')"} <= set(
        re.findall(r"\(N'[A-Z]',\s*N'[A-Z]'\)", seed)
    ):
        failures.append("deterministic hierarchy cycle is missing")
    roles = set(re.findall(r"CREATE\s+ROLE\s+(learningnemo_[a-z_]+)", security, re.IGNORECASE))
    if {role.casefold() for role in roles} != REQUIRED_ROLES:
        failures.append("database role inventory differs")
    for prohibited in (
        "db_owner",
        "db_datareader",
        "db_datawriter",
        "FROM EXTERNAL PROVIDER",
        "GRANT CONTROL",
        "GRANT ALTER",
        "KILL ",
        "PASSWORD",
    ):
        if prohibited.casefold() in combined.casefold():
            failures.append(f"prohibited SQL authority is present: {prohibited}")
    if security.count("WITH SID = __") != 5 or security.count("TYPE = E") != 5:
        failures.append("managed identity SID template differs")
    if "DENY SELECT, INSERT, UPDATE, DELETE ON SCHEMA::control" not in security:
        failures.append("direct control schema access is not denied")
    if "DENY SELECT, INSERT, UPDATE, DELETE ON SCHEMA::lab" not in security:
        failures.append("direct lab schema access is not denied")
    for procedure in (
        "control.usp_initialize_cycle_demo",
        "control.usp_record_cycle_containment_plan",
        "control.usp_record_cycle_execution",
        "control.usp_record_cycle_verification",
        "control.usp_get_cycle_demo_summary",
    ):
        body = procedure_body(control_workflow, procedure)
        if "WITH EXECUTE AS OWNER" not in body:
            failures.append(f"control workflow procedure does not execute as owner: {procedure}")
        if f"GRANT EXECUTE ON OBJECT::{procedure} TO learningnemo_control" not in control_workflow:
            failures.append(f"control workflow procedure grant differs: {procedure}")
    reconciliation = procedure_body(recovery, "control.usp_initialize_cycle_demo")
    for required in (
        "UPDATE lab.QueryRuns WITH (UPDLOCK, ROWLOCK)",
        "State = N'failed'",
        "FailureCode = COALESCE(FailureCode, N'watchdog_expired')",
        "State IN (N'starting', N'running', N'cancel_requested')",
        "WatchdogDeadline <= @now_utc",
    ):
        if required.casefold() not in reconciliation.casefold():
            failures.append(f"expired query-run reconciliation is missing: {required}")
    if "GRANT EXECUTE ON OBJECT::control.usp_initialize_cycle_demo TO learningnemo_control" not in recovery:
        failures.append("reconciled initializer grant differs")
    return failures


def main() -> int:
    try:
        schema = (SQL_DIR / "001_schema.sql").read_text(encoding="utf-8")
        procedures = (SQL_DIR / "002_procedures.sql").read_text(encoding="utf-8")
        seed = (SQL_DIR / "003_seed.sql").read_text(encoding="utf-8")
        security = (SQL_DIR / "004_security.sql.tmpl").read_text(encoding="utf-8")
        control_workflow = (SQL_DIR / "005_control_workflow.sql").read_text(encoding="utf-8")
        recovery = (SQL_DIR / "006_reconcile_expired_query_runs.sql").read_text(encoding="utf-8")
    except OSError as error:
        print(f"FAIL unable to read SQL migrations: {error}", file=sys.stderr)
        return 1
    failures = validate_sql(schema, procedures, seed, security, control_workflow, recovery)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS control, lab, and ops schemas contain the required lifecycle records")
    print("PASS unsafe recursion is isolated, read-only, MAXDOP 1, and lease-controlled")
    print("PASS safe query, watchdog state, atomic approval remediation, and narrow roles match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())