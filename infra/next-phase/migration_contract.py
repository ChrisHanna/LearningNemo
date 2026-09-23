#!/usr/bin/env python3
"""Exact inventory and ownership contract for the WP3 migration job."""

from __future__ import annotations

from collections import Counter
from typing import Any


RESOURCE_TYPES = Counter({"microsoft.app/jobs": 1})


def job_name(values: dict[str, Any]) -> str:
    return f"caj-{values['projectName']}-migrate-{values['environment']}"


def tags(values: dict[str, Any]) -> dict[str, str]:
    image_reference = str(values["imageReference"])
    return {
        "project": values["projectName"],
        "environment": values["environment"],
        "managedBy": "bicep",
        "owner": values["ownerTag"],
        "costProfile": "one-shot-consumption-job",
        "trustZone": "trusted-platform",
        "platformPhase": "wp3-database-migration",
        "disposable": "true",
        "expiresAt": values["expiresAt"],
        "imageDigest": image_reference.rsplit("@sha256:", 1)[1],
        "migrationBundleHash": values["migrationBundleSha256"],
        **values["additionalTags"],
    }


def tags_match(actual: Any, expected: dict[str, str]) -> bool:
    return isinstance(actual, dict) and all(actual.get(name) == value for name, value in expected.items())


def classify(resources: list[dict[str, Any]]) -> str:
    counts = Counter(str(item.get("type", "")).casefold() for item in resources)
    if not counts:
        return "absent"
    if counts == RESOURCE_TYPES:
        return "complete"
    return "unexpected"