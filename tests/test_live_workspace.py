import datetime as dt
import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from task_agent.console.identity import AccessProfileError, validate_signed_in_user

from task_agent.console.live_workspace import BASE_RULES, RULES, WorkspaceIdentityVerifier, WorkspaceLiveError, cloud_snapshot, parse_probe, probe_script


@pytest.mark.parametrize("invalid", ["inactive", "client", "age", "future", "subject", "reader", "scope", None])
def test_workspace_identity_rechecks_verified_token(invalid):
    verifier = WorkspaceIdentityVerifier.__new__(WorkspaceIdentityVerifier)
    verifier.settings = SimpleNamespace(public_client_id="client")
    claims = {"sub": "user", "scp": "agent.invoke tasks.execute", "roles": ["Task.Reader", "Task.Operator"]}
    result = SimpleNamespace(active=True, subject="user", client_id="client", iat=dt.datetime.now(dt.UTC).timestamp()-10)
    if invalid == "inactive": result.active = False
    if invalid == "client": result.client_id = "other"
    if invalid == "age": result.iat -= 901
    if invalid == "future": result.iat += 1000
    if invalid == "subject": claims["sub"] = "other"
    if invalid == "reader": claims["roles"] = ["Task.Reader"]
    if invalid == "scope": claims["scp"] = "agent.invoke"
    token = "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".signature"
    class Provider:
        async def verify(self, supplied):
            assert supplied == token
            return result
    verifier.provider = Provider()
    if invalid in ("inactive", "client", "age", "future", "subject"):
        with pytest.raises(WorkspaceLiveError): asyncio.run(verifier.verify(token))
    else:
        assert asyncio.run(verifier.verify(token))["allowed"] == (invalid is None)


def snapshot_inputs():
    vm = {"powerState": "VM running", "publicIps": "", "tags": {
        "project": "learningnemo", "owner": "learningnemo-portfolio",
        "expiresAt": (dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).isoformat(),
    }}
    rules = [{"name": name, "priority": values[0], "access": values[1],
              "destinationAddressPrefix": values[2], "destinationPortRange": values[3],
              "protocol": values[4], "direction": "Outbound", "sourceAddressPrefix": "*", "sourcePortRange": "*"}
             for name, values in RULES.items()]
    rules.extend({"name": name, "priority": values[0], "direction": values[1], "destinationAddressPrefix": values[2],
                  "access": "Deny", "protocol": "*", "sourceAddressPrefix": "*", "sourcePortRange": "*", "destinationPortRange": "*"}
                 for name, values in BASE_RULES.items())
    return vm, {"provisioningState": "succeeded"}, rules, {"natGateway": None}


def test_snapshot_gates_actual_state_not_prior_evidence():
    inputs = snapshot_inputs()
    result = cloud_snapshot(*inputs)
    assert result["readyForProbe"]
    assert result["gateway"] == "not-checked"
    inputs[2][0]["access"] = "Allow"
    assert not cloud_snapshot(*inputs)["readyForProbe"]


def test_managed_workspace_requires_explicit_admission_and_owned_network():
    vm, stack, rules, subnet = snapshot_inputs()
    vm['tags'].pop('expiresAt')
    vm['tags'].update(availabilityMode='operator-managed',admission='enabled')
    nat={'name':'nat-learningnemo-saw-runtime-dev','id':'/fixture/nat','properties':{'provisioningState':'Succeeded'},
        'tags':{'project':'learningnemo','owner':'learningnemo-portfolio','purpose':'runtime-approved-egress','availabilityMode':'operator-managed'}}
    subnet['natGateway']={'id':nat['id']}
    assert cloud_snapshot(vm,stack,rules,subnet,nat)['readyForProbe']
    assert cloud_snapshot(vm,stack,rules,subnet,nat)['expiresAt'] is None
    vm['tags']['admission']='paused'
    assert not cloud_snapshot(vm,stack,rules,subnet,nat)['readyForProbe']
    vm['tags']['admission']='enabled'
    nat['tags']['owner']='other'
    assert not cloud_snapshot(vm,stack,rules,subnet,nat)['readyForProbe']


def test_snapshot_rejects_additional_rules():
    inputs = snapshot_inputs()
    inputs[2].append({"name": "allow-extra"})
    assert not cloud_snapshot(*inputs)["readyForProbe"]


@pytest.mark.parametrize("name", list(BASE_RULES))
def test_snapshot_validates_baseline_denials_not_just_names(name):
    inputs = snapshot_inputs()
    next(rule for rule in inputs[2] if rule["name"] == name)["access"] = "Allow"
    assert not cloud_snapshot(*inputs)["readyForProbe"]


@pytest.mark.parametrize("change", [None, "flattened", "owner", "purpose", "expiry", "name", "association", "provisioning", "rules"])
def test_runtime_nat_requires_owned_live_association_and_unchanged_policy(change):
    vm, stack, rules, subnet = snapshot_inputs()
    nat = {"name": "nat-learningnemo-saw-runtime-dev", "id": "/fixture/runtime-nat",
           "properties": {"provisioningState": "Succeeded"},
           "tags": {"project": "learningnemo", "owner": "learningnemo-portfolio", "purpose": "runtime-approved-egress",
                    "expiresAt": (dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).isoformat()}}
    subnet["natGateway"] = {"id": nat["id"]}
    if change == "flattened": nat.update(nat.pop("properties"))
    if change == "owner": nat["tags"]["owner"] = "someone-else"
    if change == "purpose": nat["tags"]["purpose"] = "bootstrap"
    if change == "expiry": nat["tags"]["expiresAt"] = "2020-01-01T00:00:00Z"
    if change == "name": nat["name"] = "nat-learningnemo-saw-bootstrap-dev"
    if change == "association": subnet["natGateway"]["id"] = "/other/nat"
    if change == "provisioning": nat["properties"]["provisioningState"] = "Failed"
    if change == "rules": rules[0]["access"] = "Allow"
    result = cloud_snapshot(vm, stack, rules, subnet, nat)
    assert result["readyForProbe"] is (change in (None, "flattened"))
    if change in (None, "flattened"):
        assert result["nat"] == "runtime-verified"


@pytest.mark.parametrize("change", ["expiry", "public", "stopped", "owner", "nat", "stack"])
def test_snapshot_fails_closed(change):
    vm, stack, rules, subnet = snapshot_inputs()
    if change == "expiry": vm["tags"]["expiresAt"] = "2020-01-01T00:00:00Z"
    if change == "public": vm["publicIps"] = "192.0.2.1"
    if change == "stopped": vm["powerState"] = "VM deallocated"
    if change == "owner": vm["tags"]["owner"] = "other"
    if change == "nat": subnet["natGateway"] = {"id": "nat"}
    if change == "stack": stack["provisioningState"] = "failed"
    assert not cloud_snapshot(vm, stack, rules, subnet)["readyForProbe"]


def test_probe_receipt_requires_matching_nonce_and_real_results():
    response = {"value": [{"message": "PROOF_abc 998 401 403 denied\n"}]}
    assert parse_probe(response, "abc")["passed"]
    with pytest.raises(WorkspaceLiveError): parse_probe(response, "different")
    response["value"][0]["message"] = "PROOF_abc 998 000 000 denied\n"
    assert not parse_probe(response, "abc")["passed"]
    response["value"][0]["message"] = "PROOF_abc 0 401 403 denied\n"
    with pytest.raises(WorkspaceLiveError): parse_probe(response, "abc")


def test_probe_script_rejects_untrusted_endpoint():
    with pytest.raises(WorkspaceLiveError): probe_script("example.com; command", "abc")
    script = probe_script("demo.region.azurecontainerapps.io", "abc")
    assert script.startswith("#!/usr/bin/env bash\n")
    assert "sandbox ssh-config planning-demo" in script
    assert "sandbox delete" not in script
    assert "systemctl restart" not in script


def test_missing_role_claim_is_profile_rejection_not_retryable_login_error():
    claims = {"scp": "agent.invoke tasks.read tasks.execute", "sub": "user"}
    token = "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".signature"
    with pytest.raises(AccessProfileError):
        validate_signed_in_user(token)