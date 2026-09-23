import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("human_consent", ROOT / "infra/configure-human-consent.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_review_scope_is_additive_and_admin_consented():
    existing = [{"id": "other", "value": "tasks.read", "isEnabled": True}]
    scopes, identifier = MODULE.merged_scopes(existing, "11111111-1111-4111-8111-111111111111")
    assert scopes[0] == existing[0] and len(existing) == 1
    assert scopes[-1]["value"] == "plans.review" and scopes[-1]["type"] == "Admin"
    assert MODULE.merged_scopes(scopes, "11111111-1111-4111-8111-111111111111") == (scopes, identifier)


def test_existing_scope_contract_is_not_silently_changed():
    with pytest.raises(ValueError):
        MODULE.merged_scopes([{"id": "other", "value": "plans.review", "isEnabled": False}], "11111111-1111-4111-8111-111111111111")


def test_review_consent_covers_requested_scopes_without_adding_execution():
    assert MODULE.review_consent_scope('plans.review') == 'agent.invoke plans.review'
    assert MODULE.review_consent_scope('agent.invoke plans.review') == 'agent.invoke plans.review'
    assert MODULE.review_consent_scope('tasks.read plans.review') == 'agent.invoke plans.review tasks.read'
    assert 'tasks.execute' not in MODULE.review_consent_scope('plans.review')


def test_preauthorization_adds_only_review_to_the_existing_client():
    client_id = '11111111-1111-4111-8111-111111111111'
    scope_id = '22222222-2222-4222-8222-222222222222'
    original = [{'appId': client_id, 'delegatedPermissionIds': ['existing']}, {'appId': 'other', 'delegatedPermissionIds': ['unchanged']}]
    result = MODULE.preauthorize_review(original, client_id, scope_id)
    assert original[0]['delegatedPermissionIds'] == ['existing']
    assert result[0]['delegatedPermissionIds'] == ['existing', scope_id]
    assert result[1] == original[1]
    assert MODULE.preauthorize_review(result, client_id, scope_id) == result


@pytest.mark.parametrize('entries', [[], [{'appId': '11111111-1111-4111-8111-111111111111', 'delegatedPermissionIds': ['duplicate', 'duplicate']}]])
def test_preauthorization_rejects_unrecognized_client_or_ambiguous_permissions(entries):
    with pytest.raises(ValueError):
        MODULE.preauthorize_review(entries, '11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222')