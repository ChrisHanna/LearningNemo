#!/usr/bin/env python3
"""Names, tags, and exact inventory for the WP3 SQL network overlay."""

from __future__ import annotations

from collections import Counter
from typing import Any


NETWORK_RESOURCE_TYPES = Counter(
    {
        "microsoft.network/networkinterfaces": 1,
        "microsoft.network/privatednszones": 1,
        "microsoft.network/privatednszones/virtualnetworklinks": 1,
        "microsoft.network/privateendpoints": 1,
    }
)


def group_tags(values: dict[str, Any]) -> dict[str, str]:
    return {
        **resource_tags(values),
    }


def resource_tags(values: dict[str, Any]) -> dict[str, str]:
    return {
        "project": values["projectName"],
        "environment": values["environment"],
        "managedBy": "bicep",
        "owner": values["ownerTag"],
        "costProfile": "expiring-sql-private-endpoint",
        "trustZone": "trusted-platform",
        "platformPhase": "wp3-database-network",
        "disposable": "true",
        "expiresAt": values["expiresAt"],
        **values["additionalTags"],
    }


def tags_match(actual: Any, expected: dict[str, str]) -> bool:
    return isinstance(actual, dict) and all(actual.get(key) == value for key, value in expected.items())


def is_service_managed_network_interface(resource: dict[str, Any]) -> bool:
    return str(resource.get("type", "")).casefold() == "microsoft.network/networkinterfaces"


def classify(resources: list[dict[str, Any]]) -> str:
    counts = Counter(str(item.get("type", "")).casefold() for item in resources)
    if not counts:
        return "absent"
    if counts == NETWORK_RESOURCE_TYPES:
        return "complete"
    if all(counts[name] <= count for name, count in NETWORK_RESOURCE_TYPES.items()) and set(counts) <= set(
        NETWORK_RESOURCE_TYPES
    ):
        return "partial"
    return "unexpected"


def private_dns_zone_name(sql_hostname_suffix: str) -> str:
    if not sql_hostname_suffix.startswith("."):
        raise ValueError("SQL hostname suffix must begin with a dot")
    return f"privatelink{sql_hostname_suffix}"


def private_endpoint_name(values: dict[str, Any]) -> str:
    return f"pe-{values['projectName']}-sql-{values['environment']}"