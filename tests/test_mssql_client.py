from __future__ import annotations

import errno
import hashlib
import importlib.util
import socket
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest

from task_agent.control.mssql_client import MssqlProcedureClient
from task_agent.control.mssql_client import PROCEDURES
from task_agent.control.mssql_client import build_connection_string
from task_agent.control.mssql_client import managed_identity_connect
from task_agent.control.mssql_client import require_private_sql_resolution
from task_agent.control.sql_backend import SqlProcedureUnavailableError


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "apply_sql_migrations",
    ROOT / "scripts" / "apply-sql-migrations.py",
)
assert spec is not None and spec.loader is not None
apply_sql_migrations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apply_sql_migrations)
load_bundle = apply_sql_migrations.load_bundle
split_batches = apply_sql_migrations.split_batches
expected_migrations = apply_sql_migrations.expected_migrations
classify_failure = apply_sql_migrations.classify_failure
MigrationStageError = apply_sql_migrations.MigrationStageError
require_managed_identity_sql_token = apply_sql_migrations.require_managed_identity_sql_token
require_private_sql_tcp = apply_sql_migrations.require_private_sql_tcp
VerifiedTokenProvider = apply_sql_migrations.VerifiedTokenProvider
connect_target_database = apply_sql_migrations.connect_target_database
SQL_LOGIN_TIMEOUT_SECONDS = apply_sql_migrations.SQL_LOGIN_TIMEOUT_SECONDS


class FakeCursor:
    def __init__(self) -> None:
        self.timeout = 0
        self.description = [("result_code",), ("run_id",), ("state",)]
        self.statement = None
        self.parameters = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, statement, *parameters):
        self.statement = statement
        self.parameters = parameters

    def fetchall(self):
        return [("owned_query_cancelled", "run-abcdefgh", "cancelled")]


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()
        self.committed = False
        self.autocommit = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True


def test_managed_identity_connection_string_has_no_identity_or_password() -> None:
    value = build_connection_string(
        server="sql-example.database.windows.net",
        database="learningnemo",
        client_id="11111111-1111-4111-8111-111111111111",
        application_name="LearningNeMoVerifier",
    )

    assert "Authentication=" not in value
    assert "UID=" not in value
    assert "APP=" not in value
    assert "Encrypt=strict" in value
    assert "TrustServerCertificate=no" in value
    assert "Password=" not in value and "PWD=" not in value


def test_managed_identity_connection_uses_explicit_token_provider() -> None:
    calls = []
    credential = object()

    result = managed_identity_connect(
        "Server=tcp:example,1433;",
        "11111111-1111-4111-8111-111111111111",
        timeout_seconds=30,
        server="sql-example.database.windows.net",
        resolver=lambda *_args, **_kwargs: [(None, None, None, None, ("10.40.1.4", 1433))],
        credential_factory=lambda **kwargs: calls.append(("credential", kwargs)) or credential,
        connect=lambda value, **kwargs: calls.append(("connect", value, kwargs)) or "connection",
    )

    assert result == "connection"
    assert calls == [
        ("credential", {"client_id": "11111111-1111-4111-8111-111111111111"}),
        (
            "connect",
            "Server=tcp:example,1433;",
            {"token_provider": credential, "timeout": 30},
        ),
    ]


def test_sql_dns_must_resolve_only_private_addresses() -> None:
    resolver_calls = []
    private = require_private_sql_resolution(
        "sql-example.database.windows.net",
        resolver=lambda *args, **kwargs: resolver_calls.append((args, kwargs))
        or [(None, None, None, None, ("10.40.1.4", 1433))],
    )
    assert private == ("10.40.1.4",)
    assert resolver_calls == [
        (
            ("sql-example.database.windows.net", 1433),
            {"family": socket.AF_INET, "type": socket.SOCK_STREAM},
        )
    ]

    with pytest.raises(RuntimeError, match="public_address"):
        require_private_sql_resolution(
            "sql-example.database.windows.net",
            resolver=lambda *_args, **_kwargs: [(None, None, None, None, ("20.50.1.4", 1433))],
        )

    with pytest.raises(RuntimeError, match="resolution_failed"):
        require_private_sql_resolution(
            "sql-example.database.windows.net",
            resolver=lambda *_args, **_kwargs: [
                (None, None, None, None, ("10.40.1.4", 1433)),
                (None, None, None, None, ("10.40.1.5", 1433)),
            ],
        )


async def test_procedure_client_uses_fixed_statement_and_parameters() -> None:
    connection = FakeConnection()
    client = MssqlProcedureClient(
        server="sql-example.database.windows.net",
        database="learningnemo",
        client_id="11111111-1111-4111-8111-111111111111",
        application_name="LearningNeMoQueryRunner",
        connect=lambda _connection_string: connection,
    )

    rows = await client.call(
        "ops.usp_confirm_owned_query_cancelled",
        {"run_id": "run-abcdefgh", "lease_id": "lease-abcdefgh"},
    )

    assert rows[0]["result_code"] == "owned_query_cancelled"
    assert connection.cursor_instance.statement == (
        "EXEC ops.usp_confirm_owned_query_cancelled @run_id = ?, @lease_id = ?"
    )
    assert connection.cursor_instance.parameters == ("run-abcdefgh", "lease-abcdefgh")
    assert connection.cursor_instance.timeout == 15
    assert connection.autocommit is True
    assert connection.committed is False


async def test_procedure_client_rejects_parameter_substitution() -> None:
    client = MssqlProcedureClient(
        server="sql-example.database.windows.net",
        database="learningnemo",
        client_id="11111111-1111-4111-8111-111111111111",
        application_name="LearningNeMoVerifier",
        connect=lambda _connection_string: FakeConnection(),
    )

    with pytest.raises(SqlProcedureUnavailableError, match="parameter contract"):
        await client.call(
            "ops.usp_verify_cycle_recovery",
            {"safe_query_version": "cycle-safe-v1", "sql": "SELECT 1"},
        )


def test_control_procedure_catalog_uses_exact_parameter_order() -> None:
    expected = {
        "control.usp_initialize_cycle_demo": (
            "task_id", "title", "engagement_id", "workspace_id", "sponsor_subject_hash",
            "logical_agent_id", "policy_hash", "expires_at", "now_utc",
        ),
        "control.usp_record_cycle_containment_plan": (
            "task_id", "engagement_id", "workspace_id", "logical_agent_id", "run_id",
            "plan_id", "plan_hash", "operation_id", "target_resource", "safe_query_version",
            "rollback_version", "parameters_json", "created_by_hash", "now_utc",
        ),
        "control.usp_issue_approval": (
            "approval_id", "plan_id", "task_id", "engagement_id", "workspace_id",
            "logical_agent_id", "plan_hash", "operation_id", "target_resource",
            "safe_query_version", "approved_by_hash", "approved_at", "expires_at",
            "one_time_id", "receipt_hash",
        ),
        "control.usp_record_cycle_execution": (
            "execution_id", "task_id", "plan_id", "approval_id", "plan_hash",
            "safe_query_version", "workspace_id", "triggered_by_hash", "result_code",
            "receipt_hash", "started_at", "completed_at",
        ),
        "control.usp_record_cycle_verification": (
            "verification_id", "task_id", "execution_id", "plan_hash",
            "safe_query_version", "verification_profile", "workspace_id", "checks_json",
            "receipt_hash", "verified_at",
        ),
        "control.usp_get_cycle_demo_summary": ("task_id", "run_id"),
    }

    assert {name: PROCEDURES[name][1] for name in expected} == expected
    assert all(PROCEDURES[name][0].count("?") == len(order) for name, order in expected.items())


def test_migration_bundle_requires_exact_hash(tmp_path) -> None:
    path = tmp_path / "migrations.sql"
    content = b"SELECT 1;\nGO\nSELECT 2;\n"
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()

    assert split_batches(load_bundle(path, digest)) == ("SELECT 1;", "SELECT 2;")
    with pytest.raises(RuntimeError, match="hash differs"):
        load_bundle(path, "0" * 64)


def test_migration_receipt_parser_requires_exact_six_file_inventory() -> None:
    bundle = "\n".join(
        f"-- BEGIN MIGRATION {name} sha256={'a' * 64}"
        for name in (
            "001_schema.sql",
            "002_procedures.sql",
            "003_seed.sql",
            "004_security.sql",
            "005_control_workflow.sql",
            "006_reconcile_expired_query_runs.sql",
        )
    )

    assert len(expected_migrations(bundle)) == 6
    with pytest.raises(RuntimeError, match="inventory differs"):
        expected_migrations(bundle.replace("006_reconcile_expired_query_runs.sql", "007_unknown.sql"))


@pytest.mark.parametrize(
    ("error", "reason", "exit_code"),
    [
        (ModuleNotFoundError("driver"), "driver_import_failed", 20),
        (RuntimeError("connection timeout"), "sql_network_failed", 21),
        (RuntimeError("18456 login failed"), "sql_authentication_failed", 22),
        (RuntimeError("42000 incorrect syntax"), "sql_execution_failed", 23),
        (RuntimeError("migration receipt differs"), "migration_receipt_failed", 24),
        (RuntimeError("sql_private_dns_resolution_failed"), "sql_private_dns_failed", 34),
        (RuntimeError("sql_dns_resolved_public_address"), "sql_dns_not_private", 35),
        (RuntimeError("IM002 driver"), "sql_driver_not_found", 28),
        (RuntimeError("08001 connect"), "sql_odbc_prelogin_failed", 48),
        (RuntimeError("HYT00 timeout"), "sql_login_timeout", 32),
        (RuntimeError("other"), "migration_unknown_failed", 25),
    ],
)
def test_migration_failure_classification_is_identifier_free(
    error: BaseException,
    reason: str,
    exit_code: int,
) -> None:
    assert classify_failure(error) == (reason, exit_code)


def test_migration_preconnect_probes_tcp_and_managed_identity() -> None:
    calls = []

    class FakeSocket:
        def close(self):
            calls.append(("socket-close",))

    class FakeCredential:
        def get_token(self, scope):
            calls.append(("get-token", scope))
            return type("Token", (), {"token": "opaque"})()

        def close(self):
            calls.append(("credential-close",))

    require_private_sql_tcp(
        ("10.40.1.4",),
        connect=lambda address, **options: calls.append(("tcp", address, options)) or FakeSocket(),
    )
    token = require_managed_identity_sql_token(
        "11111111-1111-4111-8111-111111111111",
        credential_factory=lambda **options: calls.append(("credential", options)) or FakeCredential(),
    )

    assert calls == [
        ("tcp", ("10.40.1.4", 1433), {"timeout": 10}),
        ("socket-close",),
        ("credential", {"client_id": "11111111-1111-4111-8111-111111111111"}),
        ("get-token", "https://database.windows.net/.default"),
        ("credential-close",),
    ]
    assert token.token == "opaque"
    assert SQL_LOGIN_TIMEOUT_SECONDS == 120


def test_verified_token_provider_reuses_only_the_sql_scoped_token() -> None:
    token = type("Token", (), {"token": "opaque", "expires_on": 4102444800})()
    provider = VerifiedTokenProvider(token)

    assert provider.get_token("https://database.windows.net/.default") is token
    with pytest.raises(RuntimeError, match="sql_token_scope_failed"):
        provider.get_token("https://management.azure.com/.default")


def test_target_database_connect_retries_serverless_resume_failure() -> None:
    calls = []
    outcomes = iter([RuntimeError("40613 database unavailable"), RuntimeError("HYT00 login timeout"), "connection"])

    def connect():
        calls.append("connect")
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    assert connect_target_database(
        connect,
        attempts=3,
        retry_seconds=15,
        sleep=lambda seconds: calls.append(("sleep", seconds)),
    ) == "connection"
    assert calls == ["connect", ("sleep", 15), "connect", ("sleep", 15), "connect"]


def test_target_database_connect_does_not_retry_authentication_failure() -> None:
    calls = []

    with pytest.raises(MigrationStageError, match="sql_target_database_connect_failed"):
        connect_target_database(
            lambda: calls.append("connect") or (_ for _ in ()).throw(RuntimeError("18456 login failed")),
            attempts=3,
            sleep=lambda seconds: calls.append(("sleep", seconds)),
        )

    assert calls == ["connect"]


def test_migration_applicator_opens_only_the_target_database() -> None:
    source = (ROOT / "scripts" / "apply-sql-migrations.py").read_text(encoding="utf-8")

    assert 'database="master"' not in source
    assert source.count("connection = connect_target_database(") == 1


def test_migration_tcp_probe_retries_until_private_sql_is_ready() -> None:
    calls = []

    class FakeSocket:
        def close(self):
            calls.append("close")

    outcomes = iter([TimeoutError(), ConnectionRefusedError(), FakeSocket()])

    def connect(_address, **_options):
        outcome = next(outcomes)
        if isinstance(outcome, OSError):
            raise outcome
        return outcome

    require_private_sql_tcp(
        ("10.40.1.4",),
        attempts=3,
        retry_seconds=5,
        connect=connect,
        sleep=lambda seconds: calls.append(("sleep", seconds)),
    )

    assert calls == [("sleep", 5), ("sleep", 5), "close"]


def test_migration_tcp_probe_fails_closed_after_bounded_retries() -> None:
    sleeps = []

    with pytest.raises(MigrationStageError, match="sql_tcp_probe_timed_out"):
        require_private_sql_tcp(
            ("10.40.1.4",),
            attempts=3,
            retry_seconds=5,
            connect=lambda *_args, **_options: (_ for _ in ()).throw(TimeoutError()),
            sleep=sleeps.append,
        )

    assert sleeps == [5, 5]


@pytest.mark.parametrize(
    ("error", "reason", "exit_code"),
    [
        (TimeoutError(), "sql_tcp_connect_timed_out", 42),
        (ConnectionRefusedError(errno.ECONNREFUSED, "refused"), "sql_tcp_connection_refused", 43),
        (OSError(errno.ENETUNREACH, "unreachable"), "sql_tcp_network_unreachable", 44),
        (PermissionError(errno.EACCES, "blocked"), "sql_tcp_connect_blocked", 45),
        (OSError(errno.EAFNOSUPPORT, "address"), "sql_tcp_address_failed", 46),
        (ConnectionResetError(errno.ECONNRESET, "reset"), "sql_tcp_connection_reset", 47),
    ],
)
def test_migration_tcp_probe_classifies_safe_network_failure(error, reason, exit_code) -> None:
    with pytest.raises(MigrationStageError) as raised:
        require_private_sql_tcp(
            ("10.40.1.4",),
            attempts=1,
            connect=lambda *_args, **_options: (_ for _ in ()).throw(error),
        )

    assert classify_failure(raised.value) == (reason, exit_code)


def test_migration_tcp_probe_preserves_only_standard_errno_name() -> None:
    error = OSError(errno.EINPROGRESS, "sensitive operating-system text")

    with pytest.raises(MigrationStageError, match="^sql_tcp_os_error_einprogress$"):
        require_private_sql_tcp(
            ("10.40.1.4",),
            attempts=1,
            connect=lambda *_args, **_options: (_ for _ in ()).throw(error),
        )


@pytest.mark.parametrize(
    ("error", "reason", "exit_code"),
    [
        (RuntimeError("sql_tcp_probe_failed"), "sql_tcp_connect_failed", 29),
        (RuntimeError("sql_managed_identity_token_failed"), "sql_managed_identity_token_failed", 36),
        (RuntimeError("sql_odbc_connect_failed"), "sql_odbc_connect_failed", 37),
        (RuntimeError("Failed to set attribute 1256 before connect"), "sql_access_token_attribute_failed", 39),
        (RuntimeError("Failed to allocate environment handle"), "sql_odbc_environment_failed", 40),
        (RuntimeError("Connection handle not allocated"), "sql_odbc_handle_failed", 41),
        (RuntimeError("sql_admin_master_connect_failed"), "sql_admin_master_connect_failed", 49),
        (RuntimeError("sql_target_database_connect_failed"), "sql_target_database_connect_failed", 50),
    ],
)
def test_migration_stage_failure_classification(error, reason, exit_code) -> None:
    assert classify_failure(error) == (reason, exit_code)


@pytest.mark.parametrize(
    ("driver_error", "ddbc_error", "reason", "exit_code"),
    [
        ("Client unable to establish connection", "TCP Provider", "sql_odbc_client_connect_failed", 37),
        ("Server rejected the connection", "request denied", "sql_server_rejected_connection", 30),
        ("Communication link failure", "transport closed", "sql_link_failed", 31),
        ("Invalid authorization specification", "principal denied", "sql_authentication_failed", 22),
        ("Connection operation failed", "SSL Provider certificate verify failed", "sql_tls_failed", 38),
    ],
)
def test_migration_classifies_structured_odbc_errors(
    driver_error: str,
    ddbc_error: str,
    reason: str,
    exit_code: int,
) -> None:
    error = RuntimeError("sql_odbc_connect_failed")
    error.driver_error = driver_error
    error.ddbc_error = ddbc_error

    assert classify_failure(error) == (reason, exit_code)