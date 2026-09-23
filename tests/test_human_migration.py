import importlib.util
from pathlib import Path
from uuid import UUID

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("human_migration", ROOT / "scripts/apply-human-migrations.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PRINCIPALS = {"incident": {"clientId": "11111111-1111-4111-8111-111111111111", "objectId": "33333333-3333-4333-8333-333333333333"},
              "review": {"clientId": "22222222-2222-4222-8222-222222222222", "objectId": "44444444-4444-4444-8444-444444444444"},
              "execution": {"clientId": "55555555-5555-4555-8555-555555555555", "objectId": "66666666-6666-4666-8666-666666666666"}}


class Connection:
    def __init__(self):
        self.receipts = {name: "a" * 64 for name in MODULE.BASE_MIGRATIONS}
        self.permissions = {kind: set() for kind in PRINCIPALS}
        self.rows = []
        self.committed = False
        self.rolled_back = False
        self.collision = False
        self.users = {}
        self.autocommit = False

    def cursor(self): return self
    def close(self): pass
    def fetchall(self): return self.rows
    def fetchone(self): return self.rows[0]
    def commit(self): self.committed = True
    def rollback(self): self.rolled_back = True

    def execute(self, sql, *values):
        self.rows = []
        if sql == "SET XACT_ABORT ON; BEGIN TRANSACTION;":
            assert self.autocommit is True
        elif sql == "COMMIT TRANSACTION;":
            self.committed = True
        elif sql == "IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;":
            self.rolled_back = True
        elif sql.startswith("SELECT MigrationId"):
            self.rows = list(self.receipts.items())
        elif sql.startswith("INSERT control.SchemaMigrations"):
            self.receipts[values[0]] = values[1]
        elif sql.startswith("SELECT sid"):
            if self.collision: self.rows = [(UUID(PRINCIPALS["review"]["clientId"]).bytes_le, "E")]
            elif values[0] in self.users: self.rows = [(self.users[values[0]], "E")]
        elif sql.startswith("CREATE USER"):
            name = sql.split("[")[1].split("]")[0]
            self.users[name] = bytes.fromhex(sql.split("0x")[1].split(",")[0])
        elif sql.startswith("DROP USER"):
            del self.users[sql.split("[")[1].split("]")[0]]
        elif sql.startswith("SELECT COUNT"):
            self.rows = [(0,)]
        elif sql.startswith("GRANT EXECUTE"):
            kind = next(kind for kind in PRINCIPALS if f"[id-learningnemo-{kind}-dev]" in sql)
            procedure = sql.split("OBJECT::")[1].split()[0]
            self.permissions[kind].add(procedure)
        elif sql.startswith('REVOKE EXECUTE'):
            self.permissions['incident'].discard(sql.split('OBJECT::')[1].split()[0])
        elif sql.startswith("SELECT permission_name"):
            kind = next(kind for kind in PRINCIPALS if values[0] == f"id-learningnemo-{kind}-dev")
            self.rows = [("CONNECT", "GRANT", 0, None, None), *[("EXECUTE", "GRANT", 1, *procedure.split(".")) for procedure in self.permissions[kind]]]


def test_additive_migration_preserves_receipts_and_verifies_grants():
    connection = Connection()
    result = MODULE.apply(connection, ROOT / "infra/next-phase/review-service", PRINCIPALS)
    assert result["status"] == "verified" and connection.committed
    assert result["originalReceiptsPreserved"] == 6
    assert len(connection.receipts) == 13
    assert connection.permissions['incident'] == set(MODULE.GRANTS['incident'])
    assert not any('approval' in procedure or 'execution' in procedure for procedure in connection.permissions['incident'])
    assert not connection.rolled_back


def test_principal_collision_rolls_back():
    connection = Connection()
    connection.collision = True
    with pytest.raises(ValueError, match="ownership collision"):
        MODULE.apply(connection, ROOT / "infra/next-phase/review-service", PRINCIPALS)
    assert connection.rolled_back and not connection.committed


def test_changed_migration_cannot_be_reapplied():
    connection = Connection()
    connection.receipts["human-001_review_boundary.sql"] = "0" * 64
    with pytest.raises(ValueError, match="applied human migration differs"):
        MODULE.apply(connection, ROOT / "infra/next-phase/review-service", PRINCIPALS)
    assert connection.rolled_back and not connection.committed


def test_owned_object_id_mapping_is_repaired_to_client_id():
    connection = Connection()
    for kind, identifiers in PRINCIPALS.items():
        connection.users[f"id-learningnemo-{kind}-dev"] = UUID(identifiers["objectId"]).bytes_le
    MODULE.apply(connection, ROOT / "infra/next-phase/review-service", PRINCIPALS)
    for kind, identifiers in PRINCIPALS.items():
        assert connection.users[f"id-learningnemo-{kind}-dev"] == UUID(identifiers["clientId"]).bytes_le
    assert connection.committed