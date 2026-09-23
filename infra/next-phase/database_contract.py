#!/usr/bin/env python3
"""Shared WP3 Azure SQL names, tags, and inventory checks."""

from __future__ import annotations

from collections import Counter
from typing import Any


BASE_RESOURCE_TYPES = Counter(
    {
        "microsoft.managedidentity/userassignedidentities": 1,
        "microsoft.sql/servers": 1,
        "microsoft.sql/servers/databases": 2,
    }
)


def group_tags(values: dict[str, Any]) -> dict[str, str]:
    return {
        "project": values["projectName"],
        "environment": values["environment"],
        "managedBy": "bicep",
        "owner": values["ownerTag"],
        "costProfile": "azure-sql-free-serverless",
        "trustZone": "trusted-platform",
        "platformPhase": "wp3-database",
        "disposable": "false",
        **values["additionalTags"],
    }


def resource_tags(values: dict[str, Any]) -> dict[str, str]:
    return {
        **group_tags(values),
        "monthlyCostCeiling": str(values["monthlyCostCeiling"]),
        "freeLimit": "auto-pause-on-exhaustion",
    }


def tags_match(actual: Any, expected: dict[str, str]) -> bool:
    return isinstance(actual, dict) and all(actual.get(key) == value for key, value in expected.items())


def is_master_database(resource: dict[str, Any]) -> bool:
    return (
        str(resource.get("type", "")).casefold() == "microsoft.sql/servers/databases"
        and str(resource.get("name", "")).rsplit("/", 1)[-1].casefold() == "master"
    )


def classify(resources: list[dict[str, Any]]) -> str:
    counts = Counter(str(item.get("type", "")).casefold() for item in resources)
    if not counts:
        return "empty"
    master_count = sum(is_master_database(item) for item in resources)
    if counts == BASE_RESOURCE_TYPES and master_count == 1:
        return "complete"
    if counts <= BASE_RESOURCE_TYPES:
        return "partial"
    return "unexpected"