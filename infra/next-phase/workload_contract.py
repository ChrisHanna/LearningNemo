#!/usr/bin/env python3
"""Shared names, tags, and inventory for WP2b trusted workers."""

from __future__ import annotations

from typing import Any

from platform_contract import resource_identity


SERVICE_KEYS = {
    "diagnostic": "diagnostic",
    "query-runner": "queryRunner",
    "remediation": "remediation",
    "verifier": "verifier",
}


def app_name(values: dict[str, Any], mode: str) -> str:
    if mode not in SERVICE_KEYS:
        raise ValueError("unknown trusted worker mode")
    return f"ca-{values['projectName']}-{mode}-{values['environment']}"


def identity_name(values: dict[str, Any], mode: str) -> str:
    if mode not in SERVICE_KEYS:
        raise ValueError("unknown trusted worker mode")
    return f"id-{values['projectName']}-{mode}-{values['environment']}"


def workload_inventory(values: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        resource_identity("Microsoft.App/containerApps", app_name(values, mode))
        for mode in SERVICE_KEYS
    }


def workload_auth_inventory(values: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        resource_identity(
            "Microsoft.App/containerApps/authConfigs",
            f"{app_name(values, mode)}/current",
        )
        for mode in SERVICE_KEYS
    }


def workload_resource_tags(values: dict[str, Any], mode: str) -> dict[str, str]:
    image_reference = str(values["imageReference"])
    return {
        "project": values["projectName"],
        "environment": values["environment"],
        "managedBy": "bicep",
        "owner": values["ownerTag"],
        "costProfile": "scale-to-zero-trusted-workers",
        "trustZone": "trusted-platform",
        "platformPhase": "wp2b-trusted-workers",
        "disposable": "true",
        "expiresAt": values["expiresAt"],
        "imageDigest": image_reference.rsplit("@sha256:", 1)[1],
        "serviceMode": mode,
        **values["additionalTags"],
    }


def combined_values(config: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    shared_names = (
        "location",
        "environment",
        "projectName",
        "containerAppsEnvironmentName",
        "databaseName",
        "ownerTag",
        "additionalTags",
    )
    if any(config[name] != parameters[name] for name in shared_names):
        raise ValueError("workload configuration and materialized parameters differ")
    return {**config, **parameters}