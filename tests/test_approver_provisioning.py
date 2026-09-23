import importlib.util
from pathlib import Path
import stat
from urllib.parse import parse_qs, urlsplit

import pytest


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("approver_provisioning", ROOT / "infra/create-entra-approver.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
APP_ID = "11111111-1111-4111-8111-111111111111"


def test_approver_role_is_additive_and_stable():
    original = [{"id": "reader-role", "value": "Task.Reader", "isEnabled": True, "allowedMemberTypes": ["User"]}]
    roles, identifier, changed = module.merged_roles(original, APP_ID)
    assert changed and len(original) == 1
    assert roles[0] == original[0]
    assert roles[1]["value"] == "Task.Approver"
    assert roles[1]["allowedMemberTypes"] == ["User"]
    assert module.merged_roles(roles, APP_ID) == (roles, identifier, False)


def test_disabled_or_ambiguous_role_is_not_silently_repaired():
    roles, identifier, changed = module.merged_roles([], APP_ID)
    roles[0]["isEnabled"] = False
    with pytest.raises(module.ProvisionError): module.merged_roles(roles, APP_ID)
    roles[0]["isEnabled"] = True
    with pytest.raises(module.ProvisionError): module.merged_roles(roles * 2, APP_ID)


def test_assign_only_approver_and_preserve_other_users():
    assert module.assignment_needed([], "api", "approver")
    assert not module.assignment_needed([{"resourceId": "api", "appRoleId": "approver"}], "api", "approver")
    for role in ("reader", "operator"):
        with pytest.raises(module.ProvisionError):
            module.assignment_needed([{"resourceId": "api", "appRoleId": role}], "api", "approver")


def test_password_requires_change_and_is_written_privately(tmp_path):
    payload = module.user_payload("learningnemo-approver@example.test", "test-only-password")
    assert payload["accountEnabled"] is True
    assert payload["passwordProfile"]["forceChangePasswordNextSignIn"] is True
    assert "passwordPolicies" not in payload
    output = tmp_path / "credentials.json"
    module.write_private(output, payload, exclusive=True)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError): module.write_private(output, payload, exclusive=True)


def test_script_has_no_password_console_output_or_password_reset():
    source = (ROOT / "infra/create-entra-approver.py").read_text()
    assert '"--body", f"@{path}"' in source
    assert 'graph("DELETE"' not in source
    assert 'graph("PATCH", f"users/' not in source
    assert 'print(password)' not in source


def test_lookup_explicitly_selects_account_enabled(monkeypatch):
    user = {"id": "user", "accountEnabled": True}
    def response(method, resource):
        assert method == "GET"
        query = parse_qs(urlsplit(resource).query)
        assert query["$filter"] == ["userPrincipalName eq 'approver@example.test'"]
        assert "accountEnabled" in query["$select"][0].split(",")
        return {"value": [user]}
    monkeypatch.setattr(module, "graph", response)
    assert module.find_users("approver@example.test") == [user]