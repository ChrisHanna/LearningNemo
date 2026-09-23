#!/usr/bin/env python3
"""Discover managed identities and materialize a private Azure SQL migration bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from workload_parameters import load_config


SQL_DIR = Path(__file__).with_name("sql")
MIGRATIONS = (
    SQL_DIR / "001_schema.sql",
    SQL_DIR / "002_procedures.sql",
    SQL_DIR / "003_seed.sql",
    SQL_DIR / "004_security.sql.tmpl",
    SQL_DIR / "005_control_workflow.sql",
    SQL_DIR / "006_reconcile_expired_query_runs.sql",
)
IDENTITY_SUFFIXES = {
    "control": "control",
    "diagnostic": "diagnostic",
    "remediation": "remediation",
    "verifier": "verifier",
    "queryRunner": "query-runner",
}
PRINCIPAL_NAME = re.compile(r"^[A-Za-z0-9-]{1,128}$")
PLACEHOLDERS = {
    "control": ("__CONTROL_PRINCIPAL__", "__CONTROL_SID_HEX__"),
    "diagnostic": ("__DIAGNOSTIC_PRINCIPAL__", "__DIAGNOSTIC_SID_HEX__"),
    "remediation": ("__REMEDIATION_PRINCIPAL__", "__REMEDIATION_SID_HEX__"),
    "verifier": ("__VERIFIER_PRINCIPAL__", "__VERIFIER_SID_HEX__"),
    "queryRunner": ("__QUERY_RUNNER_PRINCIPAL__", "__QUERY_RUNNER_SID_HEX__"),
}


class MigrationError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def az_json(arguments: list[str]) -> Any:
    try:
        result = subprocess.run(
            ["az", *arguments, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MigrationError("managed identity discovery failed or timed out") from error
    if result.returncode != 0:
        raise MigrationError("managed identity discovery failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise MigrationError("managed identity discovery returned unreadable JSON") from error


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def discover(config_path: Path, output: Path) -> None:
    config = load_config(config_path)
    identities: dict[str, dict[str, str]] = {}
    for key, suffix in IDENTITY_SUFFIXES.items():
        expected_name = f"id-{config['projectName']}-{suffix}-{config['environment']}"
        identity = az_json(
            [
                "identity",
                "show",
                "--resource-group",
                config["platformResourceGroupName"],
                "--name",
                expected_name,
            ]
        )
        if identity.get("name") != expected_name:
            raise MigrationError("managed identity name differs from the database contract")
        identities[key] = {
            "name": expected_name,
            "clientId": str(identity.get("clientId", "")),
        }
    validate_identities(identities)
    write_private(output, json.dumps(identities, indent=2, sort_keys=True) + "\n")


def validate_identities(identities: Any) -> dict[str, dict[str, str]]:
    if not isinstance(identities, dict) or set(identities) != set(IDENTITY_SUFFIXES):
        raise MigrationError("database identity inventory differs")
    names: set[str] = set()
    client_ids: set[str] = set()
    for identity in identities.values():
        if not isinstance(identity, dict) or set(identity) != {"name", "clientId"}:
            raise MigrationError("database identity record differs")
        name = identity.get("name")
        client_id = identity.get("clientId")
        if not isinstance(name, str) or PRINCIPAL_NAME.fullmatch(name) is None:
            raise MigrationError("database identity name is invalid")
        try:
            parsed = uuid.UUID(str(client_id))
        except ValueError as error:
            raise MigrationError("database identity client ID is invalid") from error
        names.add(name)
        client_ids.add(str(parsed))
    if len(names) != len(IDENTITY_SUFFIXES) or len(client_ids) != len(IDENTITY_SUFFIXES):
        raise MigrationError("database identities must be distinct")
    return identities


def load_identities(path: Path) -> dict[str, dict[str, str]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MigrationError("unable to read private database identities") from error
    return validate_identities(document)


def sid_hex(client_id: str) -> str:
    return "0x" + uuid.UUID(client_id).bytes_le.hex().upper()


def migration_guard(migration_id: str, digest: str) -> str:
    return f"""
IF EXISTS (
    SELECT 1 FROM control.SchemaMigrations
    WHERE MigrationId = N'{migration_id}' AND ContentHash <> '{digest}'
) THROW 51090, 'migration_hash_mismatch', 1;
IF NOT EXISTS (SELECT 1 FROM control.SchemaMigrations WHERE MigrationId = N'{migration_id}')
    INSERT control.SchemaMigrations (MigrationId, ContentHash) VALUES (N'{migration_id}', '{digest}');
GO
"""


def materialize(identity_path: Path, output: Path) -> None:
    identities = load_identities(identity_path)
    sections: list[str] = ["-- LearningNeMo Azure SQL migration bundle. Generated from reviewed sources.\n"]
    for migration in MIGRATIONS:
        try:
            raw = migration.read_text(encoding="utf-8")
        except OSError as error:
            raise MigrationError(f"unable to read migration {migration.name}") from error
        rendered = raw
        if migration.suffix == ".tmpl":
            for key, (name_placeholder, sid_placeholder) in PLACEHOLDERS.items():
                rendered = rendered.replace(name_placeholder, identities[key]["name"])
                rendered = rendered.replace(sid_placeholder, sid_hex(identities[key]["clientId"]))
        if "__" in rendered:
            raise MigrationError(f"migration {migration.name} contains an unresolved placeholder")
        digest = sha256_bytes(rendered.encode("utf-8"))
        migration_id = migration.name.removesuffix(".tmpl")
        sections.extend(
            [
                f"-- BEGIN MIGRATION {migration_id} sha256={digest}\n",
                rendered.rstrip() + "\n",
                migration_guard(migration_id, digest),
                f"-- END MIGRATION {migration_id}\n",
            ]
        )
    write_private(output, "\n".join(sections))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    discover_parser = subparsers.add_parser("discover")
    discover_parser.add_argument("--config", type=Path, required=True)
    discover_parser.add_argument("--output", type=Path, required=True)
    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("--identities", type=Path, required=True)
    materialize_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "discover":
            discover(args.config, args.output)
            print("PASS discovered five managed identity client IDs into owner-only state")
        else:
            materialize(args.identities, args.output)
            print("PASS materialized owner-only Azure SQL migration bundle")
        return 0
    except MigrationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())