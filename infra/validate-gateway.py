#!/usr/bin/env python3
"""Validate the compiled APIM LLM gateway security contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any


EXPECTED_RESOURCE_TYPES = Counter(
    {
        "Microsoft.KeyVault/vaults/secrets": 2,
        "Microsoft.ApiManagement/service/namedValues": 2,
        "Microsoft.ApiManagement/service/apis": 1,
        "Microsoft.ApiManagement/service/apis/operations": 2,
        "Microsoft.ApiManagement/service/apis/operations/policies": 1,
        "Microsoft.ApiManagement/service/apis/policies": 1,
    }
)
VARIABLE_EXPRESSION = re.compile(r"^\[variables\('([^']+)'\)\]$")


def policy_text(template: dict[str, Any], resource: dict[str, Any]) -> str:
    value = (resource.get("properties") or {}).get("value")
    match = VARIABLE_EXPRESSION.fullmatch(str(value))
    if match is None:
        return str(value or "")
    return str((template.get("variables") or {}).get(match.group(1), ""))


def valid_xml(value: str) -> bool:
    try:
        ET.fromstring(value)
    except ET.ParseError:
        return False
    return True


def validate_template(template: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    parameters = template.get("parameters") or {}
    if set(parameters) != {
        "apiManagementName",
        "keyVaultName",
        "openAiApiKey",
        "gatewayClientKey",
    }:
        failures.append("gateway parameters differ from the existing-resource contract")
    for name in ("openAiApiKey", "gatewayClientKey"):
        parameter = parameters.get(name) or {}
        if parameter.get("type") != "securestring" or parameter.get("defaultValue") != "":
            failures.append(f"gateway parameter must be optional and secure: {name}")

    resources = template.get("resources") or []
    if not isinstance(resources, list):
        return ["compiled gateway resources must be an array"]
    resource_types = Counter(str(resource.get("type", "")) for resource in resources)
    if resource_types != EXPECTED_RESOURCE_TYPES:
        failures.append("gateway resource inventory differs")

    key_vault_secrets = [
        resource
        for resource in resources
        if resource.get("type") == "Microsoft.KeyVault/vaults/secrets"
    ]
    expected_secret_parameters = {
        "openai-api-key": "openAiApiKey",
        "llm-gateway-client-key": "gatewayClientKey",
    }
    for secret_name, parameter_name in expected_secret_parameters.items():
        matches = [resource for resource in key_vault_secrets if secret_name in str(resource.get("name", ""))]
        if len(matches) != 1:
            failures.append(f"conditional Key Vault secret is missing: {secret_name}")
            continue
        resource = matches[0]
        if (
            resource.get("condition") != f"[not(empty(parameters('{parameter_name}')))]"
            or (resource.get("properties") or {}).get("value") != f"[parameters('{parameter_name}')]"
            or ((resource.get("properties") or {}).get("attributes") or {}).get("enabled") is not True
        ):
            failures.append(f"Key Vault secret bootstrap contract differs: {secret_name}")

    named_values = [
        resource
        for resource in resources
        if resource.get("type") == "Microsoft.ApiManagement/service/namedValues"
    ]
    expected_names = {"openai-api-key", "llm-gateway-client-key"}
    if {str((resource.get("properties") or {}).get("displayName")) for resource in named_values} != expected_names:
        failures.append("gateway named-value inventory differs")
    for resource in named_values:
        properties = resource.get("properties") or {}
        display_name = properties.get("displayName")
        secret_identifier = (properties.get("keyVault") or {}).get("secretIdentifier", "")
        if (
            properties.get("secret") is not True
            or "value" in properties
            or f"secrets/{display_name}" not in str(secret_identifier)
        ):
            failures.append("gateway credentials must be secret Key Vault references")

    apis = [
        resource
        for resource in resources
        if resource.get("type") == "Microsoft.ApiManagement/service/apis"
    ]
    if len(apis) != 1:
        failures.append("gateway must define exactly one API")
    else:
        properties = apis[0].get("properties") or {}
        if (
            properties.get("path") != "llm/v1"
            or properties.get("protocols") != ["https"]
            or properties.get("serviceUrl") != "https://api.openai.com/v1"
            or properties.get("subscriptionRequired") is not False
        ):
            failures.append("gateway API route or transport differs")

    operations = {
        (resource.get("properties") or {}).get("urlTemplate"): resource
        for resource in resources
        if resource.get("type") == "Microsoft.ApiManagement/service/apis/operations"
    }
    if set(operations) != {"/chat/completions", "/guardrails/chat/completions"}:
        failures.append("agent and semantic guardrail operation inventory differs")
    elif any((resource.get("properties") or {}).get("method") != "POST" for resource in operations.values()):
        failures.append("gateway operations must be POST-only")

    api_policies = [
        policy_text(template, resource)
        for resource in resources
        if resource.get("type") == "Microsoft.ApiManagement/service/apis/policies"
    ]
    if len(api_policies) != 1 or not valid_xml(api_policies[0]):
        failures.append("gateway API policy is missing or invalid XML")
    else:
        policy = api_policies[0]
        for required in (
            "llm-gateway-client-key",
            "openai-api-key",
            "https://api.openai.com/v1",
            '<forward-request timeout="120"',
            '<set-status code="401"',
        ):
            if required not in policy:
                failures.append(f"gateway API policy is missing: {required}")

    operation_policies = [
        (resource, policy_text(template, resource))
        for resource in resources
        if resource.get("type") == "Microsoft.ApiManagement/service/apis/operations/policies"
    ]
    if len(operation_policies) != 1 or not valid_xml(operation_policies[0][1]):
        failures.append("semantic guardrail operation policy is missing or invalid XML")
    else:
        resource, policy = operation_policies[0]
        if "semantic-guardrail-chat-completions" not in str(resource.get("name", "")):
            failures.append("operation policy is not bound to the semantic guardrail route")
        required_guardrail_policy = (
            '<rewrite-uri template="/chat/completions"',
            'body["model"] = "gpt-4o-mini"',
            'body["temperature"] = 0',
            'body.Remove("max_completion_tokens")',
            'body["max_tokens"] = 3',
            'body["stream"] = false',
            'body.Remove("tools")',
            'body.Remove("tool_choice")',
            'body.Remove("functions")',
            'body.Remove("function_call")',
        )
        if policy.count("<base") != 4 or any(value not in policy for value in required_guardrail_policy):
            failures.append("semantic guardrail route must inherit policy and rewrite only its path")

    outputs = template.get("outputs") or {}
    if set(outputs) != {"llmGatewayBaseUrl", "semanticGuardrailBaseUrl"}:
        failures.append("gateway output contract differs")
    elif "/llm/v1/guardrails" not in str(outputs["semanticGuardrailBaseUrl"].get("value", "")):
        failures.append("semantic guardrail base URL differs")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read compiled gateway template: {error}", file=sys.stderr)
        return 1
    failures = validate_template(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS one APIM API exposes distinct agent and semantic guardrail operations")
    print("PASS both credentials remain Key Vault references and unauthorized requests return 401")
    print("PASS semantic guardrails inherit the gateway policy and rewrite to the existing OpenAI backend")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())