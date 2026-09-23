#!/usr/bin/env python3
"""Materialize owner-only secure ARM parameters for the LLM gateway."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


PARAMETER_SCHEMA = "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#"
RESOURCE_NAME = re.compile(r"^[A-Za-z0-9-]{1,90}$")


class GatewayParameterError(RuntimeError):
    pass


def read_value(path: Path | None, label: str) -> str:
    if path is None:
        return ""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise GatewayParameterError(f"unable to read {label}") from error
    if not value or "\n" in value or "\r" in value:
        raise GatewayParameterError(f"{label} must contain exactly one non-empty value")
    return value


def materialize(
    output: Path,
    *,
    api_management_name: str,
    key_vault_name: str,
    openai_key_file: Path | None = None,
    gateway_key_file: Path | None = None,
) -> None:
    if RESOURCE_NAME.fullmatch(api_management_name) is None:
        raise GatewayParameterError("API Management name is invalid")
    if RESOURCE_NAME.fullmatch(key_vault_name) is None:
        raise GatewayParameterError("Key Vault name is invalid")
    document = {
        "$schema": PARAMETER_SCHEMA,
        "contentVersion": "1.0.0.0",
        "parameters": {
            "apiManagementName": {"value": api_management_name},
            "keyVaultName": {"value": key_vault_name},
            "openAiApiKey": {"value": read_value(openai_key_file, "OpenAI provider key")},
            "gatewayClientKey": {"value": read_value(gateway_key_file, "gateway client key")},
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--api-management-name", required=True)
    parser.add_argument("--key-vault-name", required=True)
    parser.add_argument("--openai-key-file", type=Path)
    parser.add_argument("--gateway-key-file", type=Path)
    args = parser.parse_args()
    try:
        materialize(
            args.output,
            api_management_name=args.api_management_name,
            key_vault_name=args.key_vault_name,
            openai_key_file=args.openai_key_file,
            gateway_key_file=args.gateway_key_file,
        )
        print("PASS materialized owner-only gateway parameters", file=sys.stderr)
        return 0
    except GatewayParameterError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())