#!/usr/bin/env python3
"""Verify the deployed WP3 database base without printing Azure identifiers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from database_contract import classify
from database_contract import group_tags
from database_contract import resource_tags
from database_contract import tags_match
from database_parameters import DatabaseParameterError
from database_parameters import parameter_values


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def az_json(arguments: list[str], *, timeout: int = 60) -> Any:
    try:
        result = subprocess.run(
            ["az", *arguments, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VerificationError("Azure database verification failed or timed out") from error
    if result.returncode != 0:
        raise VerificationError("Azure database verification query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise VerificationError("Azure database verification returned unreadable JSON") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    args = parser.parse_args()
    try:
        try:
            values = parameter_values(args.parameters)
        except DatabaseParameterError as error:
            raise VerificationError(str(error)) from error
        account = az_json(["account", "show"], timeout=20)
        expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
        if expected_subscription:
            require(account.get("id") == expected_subscription, "active subscription mismatch")
        resource_group = values["databaseResourceGroupName"]
        group = az_json(["group", "show", "--name", resource_group], timeout=20)
        require(group.get("location") == values["location"], "database group location differs")
        require(tags_match(group.get("tags"), group_tags(values)), "database group tags differ")
        resources = az_json(["resource", "list", "--resource-group", resource_group], timeout=30)
        require(classify(resources) == "complete", "database base inventory differs")
        by_type = {str(item.get("type", "")).casefold(): item for item in resources}
        expected_tags = resource_tags(values)
        identity_summary = by_type["microsoft.managedidentity/userassignedidentities"]
        require(
            tags_match(identity_summary.get("tags"), {**expected_tags, "identityPurpose": "database-bootstrap-admin"}),
            "database bootstrap identity tags differ",
        )
        identity_name = str(identity_summary.get("name", ""))
        identity = az_json(
            ["identity", "show", "--resource-group", resource_group, "--name", identity_name],
            timeout=30,
        )
        require(identity.get("isolationScope") == "Regional", "database bootstrap identity isolation differs")

        server_summary = by_type["microsoft.sql/servers"]
        server_name = str(server_summary.get("name", ""))
        server = az_json(["sql", "server", "show", "--resource-group", resource_group, "--name", server_name])
        require(tags_match(server.get("tags"), expected_tags), "SQL server tags differ")
        require(server.get("publicNetworkAccess") == "Disabled", "SQL public access is enabled")
        require(server.get("minimalTlsVersion") == "1.2", "SQL minimum TLS differs")
        require(server.get("restrictOutboundNetworkAccess") == "Enabled", "SQL outbound restriction differs")
        require((server.get("identity") or {}).get("type") == "UserAssigned", "SQL server identity differs")
        ad_only = az_json(
            ["sql", "server", "ad-only-auth", "get", "--resource-group", resource_group, "--name", server_name],
            timeout=30,
        )
        require(ad_only.get("azureAdOnlyAuthentication") is True, "SQL Entra-only authentication is disabled")
        admins = az_json(
            ["sql", "server", "ad-admin", "list", "--resource-group", resource_group, "--server", server_name],
            timeout=30,
        )
        require(
            len(admins) == 1
            and admins[0].get("login") == identity_name
            and admins[0].get("administratorType") == "ActiveDirectory",
            "SQL bootstrap administrator differs",
        )

        database = az_json(
            [
                "sql",
                "db",
                "show",
                "--resource-group",
                resource_group,
                "--server",
                server_name,
                "--name",
                values["databaseName"],
            ],
            timeout=45,
        )
        require(tags_match(database.get("tags"), expected_tags), "SQL database tags differ")
        require(database.get("useFreeLimit") is True, "SQL free limit is disabled")
        require(database.get("freeLimitExhaustionBehavior") == "AutoPause", "SQL free-limit behavior differs")
        require(database.get("autoPauseDelay") == 60, "SQL auto-pause delay differs")
        require(database.get("minCapacity") == 0.5, "SQL minimum capacity differs")
        require(database.get("maxSizeBytes") == 34359738368, "SQL size cap differs")
        require(database.get("requestedBackupStorageRedundancy") == "Local", "SQL backup redundancy differs")
        require(str(database.get("currentServiceObjectiveName", "")).startswith("GP_S_Gen5_"), "SQL SKU differs")
        print("PASS dedicated regional database bootstrap identity and Entra-only administrator match")
        print("PASS SQL public access is disabled, TLS is 1.2, and outbound access is restricted")
        print("PASS free-limit serverless database auto-pauses before any billable overage")
        print("PASS exact WP3 base inventory contains no private endpoint or migration compute")
        return 0
    except VerificationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())