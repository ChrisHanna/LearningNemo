"""Classify a fixed gateway probe without printing credentials or provider bodies."""

import json
import asyncio
import os
from pathlib import Path
import subprocess

import httpx


def classify(status, document):
    error = document.get('error', {}) if isinstance(document, dict) else {}
    if not isinstance(error, dict):
        return 'unclassified-response'
    if status == 401 and error.get('message') == 'Invalid LLM gateway credential':
        return 'internal-gateway-credential-rejected'
    if status == 401 and error.get('code') == 'invalid_api_key':
        return 'provider-credential-rejected'
    if status == 200:
        return 'classifier-responded'
    return 'upstream-or-gateway-error'


def main():
    def az(*arguments):
        result = subprocess.run(['az', *arguments, '-o', 'json', '--only-show-errors'], capture_output=True, text=True, check=True, timeout=60)
        return json.loads(result.stdout)
    gateway = az('apim', 'show', '-g', 'rg-nemo-agent-dev', '-n', 'apim-nemo-8370187d')
    key = az('keyvault', 'secret', 'show', '--vault-name', 'kvnemo8370187d', '--name', 'llm-gateway-client-key')['value']
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        response = client.post(gateway['gatewayUrl'] + '/llm/v1/guardrails/chat/completions',
            headers={'Authorization': 'Bearer ' + key},
            json={'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': 'Reply No.'}], 'max_tokens': 3})
    try:
        document = response.json()
    except ValueError:
        document = {}
    print(json.dumps({'httpStatus': response.status_code, 'category': classify(response.status_code, document),
        'apimRequestId': response.headers.get('apim-request-id'), 'providerRequestId': response.headers.get('x-request-id')}))
    settings = json.loads((Path(__file__).resolve().parents[1] / '.nemo-test-client.json').read_text())
    for name in ('ENTRA_TENANT_ID', 'ENTRA_CLIENT_ID', 'ENTRA_PUBLIC_CLIENT_ID'):
        os.environ[name] = settings[name]
    os.environ.update(OPENAI_API_KEY=key, OPENAI_BASE_URL=gateway['gatewayUrl'] + '/llm/v1', OPENAI_GUARDRAIL_BASE_URL=gateway['gatewayUrl'] + '/llm/v1/guardrails')
    from nat.runtime.loader import load_config
    from task_agent.security.semantic_guardrail import semantic_guardrail_middleware
    config = load_config(Path(__file__).resolve().parents[1] / 'configs/agent.yml')
    middleware = config.middleware['semantic_input_guardrails']
    middleware = type(middleware).model_validate(middleware.model_dump(mode='json', by_alias=True, round_trip=True))
    print(json.dumps({'workerCredentialMatches': middleware.resolved_api_key() == key}))
    async def probe():
        async with semantic_guardrail_middleware(middleware, None) as guardrail:
            try:
                allowed = await guardrail._is_allowed({'input_message': 'List the current demo tasks. Do not execute or reset any tasks.'})
                print(json.dumps({'isolatedMiddlewareAllowed': allowed}))
            except Exception as error:
                print(json.dumps({'isolatedMiddlewareError': type(error).__name__}))
    asyncio.run(probe())


if __name__ == '__main__':
    main()