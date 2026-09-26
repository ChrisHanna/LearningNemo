#!/usr/bin/env python3
"""Load the agent configuration without credentials or network calls."""

from __future__ import annotations

import os
from pathlib import Path


PLACEHOLDER_ENVIRONMENT = {
    "ENTRA_TENANT_ID": "00000000-0000-4000-8000-000000000001",
    "ENTRA_CLIENT_ID": "00000000-0000-4000-8000-000000000002",
    "ENTRA_PUBLIC_CLIENT_ID": "00000000-0000-4000-8000-000000000003",
    "OPENAI_BASE_URL": "https://example.invalid/llm/v1",
    "OPENAI_GUARDRAIL_BASE_URL": "https://example.invalid/llm/v1/guardrails",
    "OPENAI_API_KEY": "validation-only",
    "NAT_TELEMETRY_ENABLED": "false",
}


def main() -> int:
    for name, value in PLACEHOLDER_ENVIRONMENT.items():
        os.environ.setdefault(name, value)

    from nat.runtime.loader import load_config

    repository = Path(__file__).resolve().parents[1]
    config = load_config(repository / "configs" / "agent.yml")
    if set(config.llms) != {"openai_llm"}:
        raise RuntimeError("agent LLM inventory differs")
    guardrail = config.middleware["semantic_input_guardrails"]
    if guardrail.resolved_api_key() != os.environ["OPENAI_API_KEY"]:
        raise RuntimeError("semantic guardrail credential was not resolved from its configured environment variable")
    restored = type(guardrail).model_validate(guardrail.model_dump(mode='json', by_alias=True, round_trip=True))
    if restored.resolved_api_key() != os.environ['OPENAI_API_KEY']:
        raise RuntimeError('guardrail credential did not survive the served-worker config handoff')
    if (
        str(guardrail.base_url).rstrip("/")
        != "https://example.invalid/llm/v1/guardrails"
        or guardrail.timeout_seconds != 15
        or guardrail.max_input_chars != 4000
    ):
        raise RuntimeError("semantic guardrail configuration differs")
    if list(config.workflow.middleware) != [
        "authenticate_entra",
        "local_input_sanitization",
        "semantic_input_guardrails",
        "response_output_rails",
    ]:
        raise RuntimeError("workflow security middleware order differs")
    for name in ("list_tasks", "execute_task", "reset_tasks"):
        if list(config.functions[name].middleware)[-1:] != ["tool_execution_rails"]:
            raise RuntimeError(f"{name} execution rails differ")
    print("PASS agent configuration uses local PII sanitization before APIM semantic guardrails")
    print("PASS semantic and agent model calls use distinct APIM operations")
    print("PASS tool execution rails and response output rails are configured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())