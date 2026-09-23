#!/usr/bin/env python3
"""Shared names, tags, and inventories for the WP2a platform base."""

from __future__ import annotations

from typing import Any


IDENTITY_PURPOSES = {
    "control": "control-plane",
    "diagnostic": "diagnostic-api",
    "remediation": "remediation-broker",
    "verifier": "independent-verifier",
    "query-runner": "demo-query-runner",
    "workspace-controller": "workspace-controller",
}


def resource_identity(resource_type: Any, name: Any) -> tuple[str, str]:
    return str(resource_type).casefold(), str(name).casefold()


def foundation_inventory(values: dict[str, Any]) -> set[tuple[str, str]]:
    project = values["projectName"]
    environment = values["environment"]
    return {
        resource_identity(
            "Microsoft.Network/networkSecurityGroups",
            f"nsg-vnet-{project}-platform-{environment}-container-apps",
        ),
        resource_identity(
            "Microsoft.Network/virtualNetworks",
            f"vnet-{project}-platform-{environment}",
        ),
    }


def identity_inventory(values: dict[str, Any]) -> set[tuple[str, str]]:
    project = values["projectName"]
    environment = values["environment"]
    identities = {
        resource_identity(
            "Microsoft.ManagedIdentity/userAssignedIdentities",
            f"id-{project}-{suffix}-{environment}",
        )
        for suffix in IDENTITY_PURPOSES
    }
    return identities


def runtime_inventory(values: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        resource_identity(
            "Microsoft.App/managedEnvironments",
            values["containerAppsEnvironmentName"],
        )
    }


def wp2a_inventory(values: dict[str, Any]) -> set[tuple[str, str]]:
    return identity_inventory(values) | runtime_inventory(values)


def platform_inventory(values: dict[str, Any]) -> set[tuple[str, str]]:
    return foundation_inventory(values) | wp2a_inventory(values)


def foundation_resource_tags(values: dict[str, Any]) -> dict[str, str]:
    return {
        "project": values["projectName"],
        "environment": values["environment"],
        "managedBy": "bicep",
        "owner": values["ownerTag"],
        "costProfile": "low-cost-poc",
        **values["additionalTags"],
    }


def identity_resource_tags(values: dict[str, Any]) -> dict[str, str]:
    return {
        "project": values["projectName"],
        "environment": values["environment"],
        "managedBy": "bicep",
        "owner": values["ownerTag"],
        "costProfile": "identity-only-no-compute",
        "monthlyCostCeiling": str(values["monthlyCostCeiling"]),
        "trustZone": "trusted-platform",
        "platformPhase": "wp2a-identities",
        "plannedRuntimeEnvironment": values["containerAppsEnvironmentName"],
        **values["additionalTags"],
    }


def runtime_resource_tags(values: dict[str, Any]) -> dict[str, str]:
    tags = {
        "project": values["projectName"],
        "environment": values["environment"],
        "managedBy": "bicep",
        "owner": values["ownerTag"],
        "costProfile": "vnet-integrated-runtime-envelope",
        "monthlyCostCeiling": str(values["monthlyCostCeiling"]),
        "trustZone": "trusted-platform",
        "platformPhase": "wp2a-runtime",
        "disposable": "true",
        **values["additionalTags"],
    }
    expires_at = values.get("expiresAt")
    if isinstance(expires_at, str) and expires_at != "__RUNTIME__":
        tags["expiresAt"] = expires_at
    return tags


def platform_group_tags(values: dict[str, Any]) -> dict[str, str]:
    return {
        **foundation_resource_tags(values),
        "trustZone": "trusted-platform",
        "disposable": "false",
    }


def infrastructure_resource_group_name(values: dict[str, Any]) -> str:
    return f"mrg-{values['projectName']}-container-apps-{values['environment']}"


def expected_resource_tags(values: dict[str, Any]) -> dict[tuple[str, str], dict[str, str]]:
    expected = {
        identity: foundation_resource_tags(values)
        for identity in foundation_inventory(values)
    }
    identity_tags = identity_resource_tags(values)
    runtime_tags = runtime_resource_tags(values)
    expected.update({identity: identity_tags for identity in identity_inventory(values)})
    expected.update({identity: runtime_tags for identity in runtime_inventory(values)})
    project = values["projectName"]
    environment = values["environment"]
    for suffix, purpose in IDENTITY_PURPOSES.items():
        identity = resource_identity(
            "Microsoft.ManagedIdentity/userAssignedIdentities",
            f"id-{project}-{suffix}-{environment}",
        )
        expected[identity] = {**identity_tags, "identityPurpose": purpose}
    return expected