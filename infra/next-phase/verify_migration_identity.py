#!/usr/bin/env python3
"""Verify the temporary SQL migration identity scope without printing identifiers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from migration_identity_parameters import MigrationIdentityParameterError
from migration_identity_parameters import parameter_values


class MigrationIdentityVerificationError(RuntimeError):
    pass


def az_json(arguments: list[str]) -> Any:
    result = subprocess.run(
        ["az", *arguments, "--output", "json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode != 0:
        raise MigrationIdentityVerificationError("migration identity verification query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise MigrationIdentityVerificationError("migration identity verification returned unreadable JSON") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--resource-group", required=True)
    args = parser.parse_args()
    try:
        try:
            values = parameter_values(args.parameters)
        except MigrationIdentityParameterError as error:
            raise MigrationIdentityVerificationError(str(error)) from error
        account = az_json(["account", "show"])
        expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
        if expected_subscription and account.get("id") != expected_subscription:
            raise MigrationIdentityVerificationError("active subscription mismatch")
        identity = az_json(
            [
                "identity",
                "show",
                "--resource-group",
                args.resource_group,
                "--name",
                f"id-{values['projectName']}-sql-admin-{values['environment']}",
            ]
        )
        tags = identity.get("tags") or {}
        if identity.get("isolationScope") != "None":
            raise MigrationIdentityVerificationError("SQL migration identity is not cross-region enabled")
        if (
            tags.get("temporaryCrossRegionUse") != "sql-migration-job"
            or tags.get("migrationScopeExpiresAt") != values["expiresAt"]
        ):
            raise MigrationIdentityVerificationError("SQL migration identity expiry tags differ")
        print("PASS SQL bootstrap identity is temporarily cross-region enabled with expiry")
        return 0
    except MigrationIdentityVerificationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())