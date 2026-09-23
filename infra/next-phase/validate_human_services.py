#!/usr/bin/env python3
"""Validate the isolated human-service template and bounded deployment inputs."""

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from uuid import UUID


def validate(template: dict, parameters: dict, *, subscription: str, now: datetime) -> None:
    UUID(subscription)
    values = {name: entry["value"] for name, entry in parameters["parameters"].items()}
    if set(values) - {"initiationEnabled", "diagnosticAudience", "queryAudience", "brokerAudience", "verifierAudience"} != {"location", "environmentId", "registryServer", "image", "expiresAt", "tenantId", "apiClientId", "publicClientId", "sqlServer", "sqlDatabase", "deployApps"}:
        raise ValueError("human-service parameter contract differs")
    if type(values.get("initiationEnabled", False)) is not bool:
        raise ValueError("initiationEnabled must be a boolean")
    if values.get("initiationEnabled"):
        for name in ("diagnosticAudience", "queryAudience"):
            UUID(values[name])
    for name in ("tenantId", "apiClientId", "publicClientId"):
        UUID(values[name])
    expected_environment = f"/subscriptions/{subscription}/resourceGroups/rg-learningnemo-platform-dev/providers/Microsoft.App/managedEnvironments/cae-learningnemo-dev"
    if values["environmentId"].casefold() != expected_environment.casefold() or values["location"] != "eastus":
        raise ValueError("expected existing demo environment in selected subscription")
    if re.fullmatch(r"[a-z0-9]{5,50}\.azurecr\.io", values["registryServer"]) is None:
        raise ValueError("invalid registry")
    if re.fullmatch(re.escape(values["registryServer"]) + r"/learningnemo/cloud-human@sha256:[0-9a-f]{64}", values["image"]) is None:
        raise ValueError("human service image must be digest pinned in the selected registry")
    if re.fullmatch(r"[a-z0-9-]{1,63}\.database\.windows\.net", values["sqlServer"]) is None or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", values["sqlDatabase"]) is None:
        raise ValueError("invalid private SQL configuration")
    expiry = datetime.fromisoformat(values["expiresAt"].replace("Z", "+00:00"))
    if now.tzinfo is None or expiry.tzinfo is None or not 300 < (expiry - now).total_seconds() <= 7200:
        raise ValueError("explicit five-minute to two-hour lease required")
    if type(values["deployApps"]) is not bool:
        raise ValueError("deployApps must be a boolean")
    if template["variables"]["services"] != ["incident", "review", "execution"]:
        raise ValueError("unexpected service set")
    resources = template["resources"]
    if len(resources) != 2 or {item["type"] for item in resources} != {"Microsoft.ManagedIdentity/userAssignedIdentities", "Microsoft.App/containerApps"}:
        raise ValueError("only two identity/app loops are permitted")
    app = next(item for item in resources if item["type"] == "Microsoft.App/containerApps")
    if app.get("condition") != "[parameters('deployApps')]":
        raise ValueError("apps must be explicitly enabled after permissions are reviewed")
    properties = app["properties"]
    configuration = properties["configuration"]
    ingress = configuration["ingress"]
    if ingress != {"external": False, "allowInsecure": False, "targetPort": 8080, "transport": "http"} or configuration["secrets"]:
        raise ValueError("private HTTPS ingress without stored secrets required")
    if app["identity"]["type"] != "UserAssigned" or len(app["identity"]["userAssignedIdentities"]) != 1:
        raise ValueError("one separate managed identity per service required")
    if properties["template"]["scale"] != {"minReplicas": 1, "maxReplicas": 1}:
        raise ValueError("single-replica demo scale required")
    containers = properties["template"]["containers"]
    if len(containers) != 1 or containers[0]["image"] != "[parameters('image')]":
        raise ValueError("only the pinned human image is permitted")
    if containers[0]["command"] != ["python", "-m", "[format('task_agent.console.{0}_service', variables('services')[copyIndex()])]"]:
        raise ValueError("fixed human-service entry point required")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--subscription", required=True)
    args = parser.parse_args()
    validate(json.loads(args.template.read_text()), json.loads(args.parameters.read_text()), subscription=args.subscription, now=datetime.now(UTC))
    print("PASS private identity-separated human services, fixed image, bounded lease, and no implicit grants")
    print("INFO SQL migration, exact grants, consent, and live transaction tests remain separate gates")


if __name__ == "__main__":
    main()