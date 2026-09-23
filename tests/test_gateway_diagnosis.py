import importlib.util
from pathlib import Path


SPEC = importlib.util.spec_from_file_location('gateway_diagnosis', Path(__file__).parents[1] / 'scripts/diagnose-agent-gateway.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_gateway_diagnosis_never_relays_provider_error_text():
    assert MODULE.classify(401, {'error': {'code': 'invalid_api_key', 'message': 'private key snippet'}}) == 'provider-credential-rejected'
    assert MODULE.classify(401, {'error': {'message': 'Invalid LLM gateway credential'}}) == 'internal-gateway-credential-rejected'
    assert MODULE.classify(200, {'choices': []}) == 'classifier-responded'