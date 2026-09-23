#!/usr/bin/env python3
"""Summarize Container Apps system logs without emitting cloud identifiers."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


CATEGORIES = {
    "image-pull": re.compile(r"image|manifest|pull", re.IGNORECASE),
    "registry-auth": re.compile(r"registry|unauthori[sz]ed|acr|credential", re.IGNORECASE),
    "managed-identity": re.compile(r"managed identity|identity operation|federated", re.IGNORECASE),
    "secret-volume": re.compile(r"secret|volume|mount", re.IGNORECASE),
    "scheduling": re.compile(r"schedul|replica|pod|container", re.IGNORECASE),
    "quota": re.compile(r"quota|insufficient|cpu|memory", re.IGNORECASE),
    "network": re.compile(r"dns|network|connect|timeout|tls", re.IGNORECASE),
}
REDACTIONS = (
    (re.compile(r"/subscriptions/[^\s\"']+", re.IGNORECASE), "<ARM-ID>"),
    (re.compile(r"https?://[^\s\"']+", re.IGNORECASE), "<URL>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", re.IGNORECASE), "<ID>"),
    (re.compile(r"sha256:[0-9a-f]{64}", re.IGNORECASE), "<DIGEST>"),
    (re.compile(r"\b(?:cae|caj|ca|rg|id|sql|vnet|snet|cr)-[a-z0-9-]+\b", re.IGNORECASE), "<RESOURCE>"),
    (re.compile(r"\b[a-z0-9]{5,50}\.azurecr\.io\b", re.IGNORECASE), "<REGISTRY>"),
    (re.compile(r"\b[a-z0-9-]{1,63}\.database\.windows\.net\b", re.IGNORECASE), "<SQL-HOST>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}T\S+\b"), "<TIME>"),
)


def string_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        values.append(value)
    elif isinstance(value, dict):
        for child in value.values():
            values.extend(string_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(string_values(child))
    return values


def sanitize(value: str) -> str:
    result = value
    for pattern, replacement in REDACTIONS:
        result = pattern.sub(replacement, result)
    result = re.sub(r"\s+", " ", result).strip()
    return result[:500]


def summarize(document: Any) -> tuple[Counter[str], list[str]]:
    records = document if isinstance(document, list) else [document]
    counts: Counter[str] = Counter()
    details_by_category: dict[str, list[str]] = {}
    for record in records:
        text = " ".join(string_values(record))
        category = next((name for name, pattern in CATEGORIES.items() if pattern.search(text)), "unknown")
        counts[category] += 1
        sanitized = sanitize(text)
        details = details_by_category.setdefault(category, [])
        detail = f"{category}: {sanitized}"
        if sanitized and detail not in details:
            details.append(detail)
            del details[:-3]
    return counts, [detail for name in sorted(details_by_category) for detail in details_by_category[name]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_file", type=Path)
    args = parser.parse_args()
    try:
        content = args.log_file.read_text(encoding="utf-8-sig")
        try:
            document = json.loads(content)
        except json.JSONDecodeError:
            document = []
            for line in content.splitlines():
                if not line.strip():
                    continue
                try:
                    document.append(json.loads(line))
                except json.JSONDecodeError:
                    document.append(line)
    except OSError as error:
        print(f"FAIL unable to read Container Apps logs: {error}", file=sys.stderr)
        return 1
    counts, details = summarize(document)
    for category, count in sorted(counts.items()):
        print(f"{category}={count}")
    for detail in details:
        print(detail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())