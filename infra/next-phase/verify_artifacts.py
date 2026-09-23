#!/usr/bin/env python3
"""Verify the live WP3 artifact registry and exact pull-role assignments."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from artifact_contract import classify
from artifact_contract import tags
from artifact_contract import tags_match
from artifact_parameters import ArtifactParameterError
from artifact_parameters import parameter_values


ACR_PULL_ROLE = "7f951dda-4ed3-4680-a7ca-43fe172d538d"


class ArtifactVerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ArtifactVerificationError(message)


def invoice_pull_principals() -> set[str]:
    group = az_json(["group", "show", "--name", "rg-learningnemo-invoice-dev"], timeout=20)
    expected_tags = {"owner": "learningnemo-portfolio", "project": "learningnemo", "purpose": "invoice-agent-workflow"}
    require(all(group.get("tags", {}).get(key) == value for key, value in expected_tags.items()), "invoice group ownership differs")
    principals = set()
    for service in ("operator", "review", "planning", "execution", "verifier"):
        identity = az_json(["identity", "show", "--resource-group", "rg-learningnemo-invoice-dev",
            "--name", f"id-learningnemo-invoice-{service}-dev"], timeout=30)
        require(all(identity.get("tags", {}).get(key) == value for key, value in expected_tags.items()), "invoice identity ownership differs")
        principals.add(str(identity.get("principalId", "")).casefold())
    require("" not in principals and len(principals) == 5, "invoice identity inventory differs")
    return principals


def az_json(arguments: list[str], *, timeout: int = 60) -> Any:
    category = " ".join(value for value in arguments[:2] if not value.startswith("--"))
    try:
        result = subprocess.run(
            ["az", *arguments, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ArtifactVerificationError(f"artifact verification query failed: {category}") from error
    if result.returncode != 0:
        raise ArtifactVerificationError(f"artifact verification query failed: {category}")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise ArtifactVerificationError(f"artifact verification returned unreadable JSON: {category}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--allow-cloud-demo", action="store_true")
    parser.add_argument("--allow-human-services", action="store_true")
    parser.add_argument("--allow-invoice-services", action="store_true")
    args = parser.parse_args()
    try:
        try:
            values = parameter_values(args.parameters)
        except ArtifactParameterError as error:
            raise ArtifactVerificationError(str(error)) from error
        account = az_json(["account", "show"], timeout=20)
        expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
        require(not expected_subscription or account.get("id") == expected_subscription, "active subscription mismatch")
        group_name = values["artifactResourceGroupName"]
        group = az_json(["group", "show", "--name", group_name], timeout=20)
        require(group.get("location") == values["location"], "artifact group location differs")
        require(tags_match(group.get("tags"), tags(values)), "artifact group tags differ")
        resources = az_json(["resource", "list", "--resource-group", group_name], timeout=30)
        require(classify(resources) == "complete", "artifact registry inventory differs")
        registry_name = str(resources[0].get("name", ""))
        registry = az_json(
            [
                "resource",
                "show",
                "--resource-group",
                group_name,
                "--resource-type",
                "Microsoft.ContainerRegistry/registries",
                "--api-version",
                "2023-07-01",
                "--name",
                registry_name,
            ],
            timeout=30,
        )
        require(tags_match(registry.get("tags"), tags(values)), "artifact registry tags differ")
        properties = registry.get("properties") or {}
        require((registry.get("sku") or {}).get("name") == "Basic", "artifact registry SKU differs")
        require(properties.get("adminUserEnabled") is False, "artifact registry admin access is enabled")
        require(properties.get("anonymousPullEnabled") in (None, False), "artifact anonymous pull is enabled")
        require(properties.get("publicNetworkAccess") == "Enabled", "artifact build/pull endpoint is disabled")
        registry_id = str(registry.get("id", ""))
        expected_principals: set[str] = set()
        for resource_group, suffix in (
            (values["platformResourceGroupName"], "diagnostic"),
            (values["platformResourceGroupName"], "query-runner"),
            (values["platformResourceGroupName"], "remediation"),
            (values["platformResourceGroupName"], "verifier"),
            (values["databaseResourceGroupName"], "sql-admin"),
        ):
            identity = az_json(
                [
                    "identity",
                    "show",
                    "--resource-group",
                    resource_group,
                    "--name",
                    f"id-{values['projectName']}-{suffix}-{values['environment']}",
                ],
                timeout=30,
            )
            expected_principals.add(str(identity.get("principalId", "")).casefold())
        if args.allow_cloud_demo:
            for service in ("dashboard", "controller", "agent"):
                identity = az_json([
                    "identity", "show", "--resource-group", "rg-learningnemo-demo-dev",
                    "--name", f"id-learningnemo-cloud-{service}-dev",
                ], timeout=30)
                require(
                    identity.get("tags", {}).get("project") == "learningnemo"
                    and identity.get("tags", {}).get("owner") == "learningnemo-portfolio"
                    and identity.get("tags", {}).get("identityPurpose") == f"cloud-demo-{service}",
                    "cloud demo pull identity ownership differs",
                )
                expected_principals.add(str(identity.get("principalId", "")).casefold())
        if args.allow_human_services:
            require(args.allow_cloud_demo, "human-service verification requires the cloud-demo scope")
            human_group = az_json(["group", "show", "--name", "rg-learningnemo-human-dev"], timeout=20)
            require(human_group.get("tags", {}).get("owner") == "learningnemo-portfolio"
                    and human_group.get("tags", {}).get("purpose") == "human-handoff", "human-service group ownership differs")
            for service in ("incident", "review", "execution"):
                identity = az_json(["identity", "show", "--resource-group", "rg-learningnemo-human-dev",
                    "--name", f"id-learningnemo-{service}-dev"], timeout=30)
                require(identity.get("tags", {}).get("owner") == "learningnemo-portfolio"
                    and identity.get("tags", {}).get("project") == "learningnemo"
                    and identity.get("tags", {}).get("costProfile") == "scheduled-human-handoff",
                    "human-service identity ownership differs")
                expected_principals.add(str(identity.get("principalId", "")).casefold())
        if args.allow_invoice_services:
            require(args.allow_human_services, "invoice verification requires the human-service scope")
            expected_principals.update(invoice_pull_principals())
        assignments = az_json(
            [
                "role",
                "assignment",
                "list",
                "--scope",
                registry_id,
                "--fill-principal-name",
                "false",
                "--fill-role-definition-name",
                "false",
            ],
            timeout=45,
        )
        direct_pull_principals = {
            str(item.get("principalId", "")).casefold()
            for item in assignments
            if str(item.get("scope", "")).casefold() == registry_id.casefold()
            and str(item.get("roleDefinitionId", "")).casefold().endswith(ACR_PULL_ROLE)
        }
        require(direct_pull_principals == expected_principals, "artifact AcrPull identity set differs")
        require("" not in expected_principals and len(expected_principals) == (16 if args.allow_invoice_services else 11 if args.allow_human_services else 8 if args.allow_cloud_demo else 5), "artifact identity inventory differs")
        print("PASS live Basic ACR denies admin and anonymous access")
        print("PASS exact artifact pull-identity allowlist matches the selected deployment scope")
        print("PASS deployed WP3 artifact registry verification complete")
        return 0
    except ArtifactVerificationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())