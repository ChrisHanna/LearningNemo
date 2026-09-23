import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location('invoice_identity', Path(__file__).parents[1]/'infra/next-phase/configure_invoice_verifier_identity.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_verifier_role_is_application_only_and_preserves_human_roles():
    original = [{'id':'existing','value':'Task.Approver','allowedMemberTypes':['User'],'isEnabled':True}]
    roles, identifier = MODULE.merge_role(original, '8370187d-74f3-4c38-8b20-4d9a1018b8a7')
    assert original == roles[:-1]
    assert roles[-1]['allowedMemberTypes'] == ['Application']
    assert roles[-1]['value'] == 'Invoice.Verify'
    assert MODULE.merge_role(roles, '8370187d-74f3-4c38-8b20-4d9a1018b8a7') == (roles, identifier)
    roles[-1]['allowedMemberTypes'].append('User')
    with pytest.raises(ValueError, match='collision'):
        MODULE.merge_role(roles, '8370187d-74f3-4c38-8b20-4d9a1018b8a7')