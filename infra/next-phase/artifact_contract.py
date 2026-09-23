#!/usr/bin/env python3
"""Exact inventory and ownership contract for the WP3 artifact registry."""

from __future__ import annotations

from collections import Counter
from typing import Any


RESOURCE_TYPES = Counter({"microsoft.containerregistry/registries": 1})


def tags(values: dict[str, Any]) -> dict[str, str]:
    return {
        "project": values["projectName"],
        "environment": values["environment"],
        "managedBy": "bicep",
        "owner": values["ownerTag"],
        "costProfile": "expiring-basic-container-registry",
        "monthlyCostCeiling": str(values["monthlyCostCeiling"]),
        "trustZone": "trusted-platform",
        "platformPhase": "wp3-artifacts",
        "disposable": "true",
        "expiresAt": values["expiresAt"],
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