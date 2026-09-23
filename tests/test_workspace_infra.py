from __future__ import annotations

import base64
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).parents[1]
PHASE_DIR = ROOT / "infra" / "next-phase"
CONFIG = PHASE_DIR / "environments" / "dev.workspace.config.json"
sys.path.insert(0, str(PHASE_DIR))

import foundation_parameters
import capture_workspace_diagnostics
import preflight_workspace
import summarize_workspace_failure
import validate_workspace_egress_what_if
import validate_workspace_lock_what_if
import validate_workspace_what_if
import validate_workspace_bootstrap_what_if
import workspace_parameters
import verify_workspace


def load_validator(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PHASE_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_workspace = load_validator("validate_workspace", "validate-workspace.py")
validate_workspace_runtime_lock = load_validator(
    "validate_workspace_runtime_lock",
    "validate-workspace-runtime-lock.py",
)


def timestamp(now: dt.datetime, *, hours: int) -> str:
    return (now + dt.timedelta(hours=hours)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_database_state(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "resourceGroupName": "rg-learningnemo-data-dev",
                "serverName": "sql-learningnemo-generated",
                "databaseName": "learningnemo",
                "sqlAdminIdentityName": "id-learningnemo-sql-admin-dev",
            }
        ),
        encoding="utf-8",
    )


def materialize_workspace(tmp_path: Path, now: dt.datetime) -> Path:
    database = tmp_path / "database.json"
    write_database_state(database)
    ssh_key = tmp_path / "id_ed25519.pub"
    ssh_key.write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK0123456789abcdefghijklmnopqrstuv learningnemo\n",
        encoding="utf-8",
    )
    output = tmp_path / "workspace.parameters.json"
    workspace_parameters.materialize(
        CONFIG,
        database,
        ssh_key,
        output,
        timestamp(now, hours=1),
    )
    return output


def test_workspace_parameters_pin_supply_chain_and_are_owner_only(tmp_path: Path) -> None:
    now = dt.datetime.now(dt.UTC)
    output = materialize_workspace(tmp_path, now)

    values = workspace_parameters.parameter_values(output)
    assert values["imageVersion"] == "24.04.202608270"
    assert values["openShellVersion"] == "0.0.116"
    assert values["openShellPackageSha256"] == workspace_parameters.PACKAGE_SHA256
    assert values["sandboxImageReference"] == workspace_parameters.SANDBOX_IMAGE
    assert values["supervisorImageReference"] == workspace_parameters.SUPERVISOR_IMAGE
    assert values["sqlServerHostname"] == "sql-learningnemo-generated.database.windows.net"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600

    base = tmp_path / "base.parameters.json"
    bootstrap = tmp_path / "bootstrap.parameters.json"
    workspace_parameters.project(output, base, "base")
    workspace_parameters.project(output, bootstrap, "bootstrap")
    base_document = json.loads(base.read_text(encoding="utf-8"))
    bootstrap_document = json.loads(bootstrap.read_text(encoding="utf-8"))
    assert set(base_document["parameters"]) == workspace_parameters.BASE_PARAMETERS
    assert set(bootstrap_document["parameters"]) == workspace_parameters.BOOTSTRAP_PARAMETERS
    assert "sqlServerHostname" not in base_document["parameters"]
    assert "sshPublicKey" not in bootstrap_document["parameters"]
    assert stat.S_IMODE(base.stat().st_mode) == 0o600
    assert stat.S_IMODE(bootstrap.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "status,authentication,expected",
    [("connected", "authenticated", 0), ("not_configured", "authenticated", 1),
     ("disconnected", "authenticated", 1), ("connected", "failed", 1)],
)
def test_runtime_probe_checks_gateway_semantics(status: str, authentication: str, expected: int) -> None:
    source = verify_workspace.runtime_probe_script("sawadmin")
    check = source.split("| python3 -c '\n", 1)[1].split("\n'", 1)[0]
    response = {
        "gateway": "openshell",
        "server": "https://127.0.0.1:17670",
        "status": status,
        "authentication": {"status": authentication},
    }
    result = subprocess.run(
        [sys.executable, "-c", check], input=json.dumps(response), text=True,
        capture_output=True, timeout=5,
    )
    assert result.returncode == expected


def test_workspace_bootstrap_retry_is_exactly_scoped() -> None:
    resource_id = "/subscriptions/demo/resourceGroups/saw/providers/Microsoft.Compute/virtualMachines/vm/runCommands/bootstrap-openshell"
    change = {
        "resourceId": resource_id,
        "changeType": "Modify",
        "after": {"type": "Microsoft.Compute/virtualMachines/runCommands"},
    }
    document = {"changes": [change]}
    assert validate_workspace_bootstrap_what_if.validate(document)
    assert validate_workspace_bootstrap_what_if.validate(document, existing_resource_id=resource_id) == []
    assert validate_workspace_bootstrap_what_if.validate(document, existing_resource_id=resource_id + "-other")
    for operation in ("Create", "Delete"):
        change["changeType"] = operation
        assert validate_workspace_bootstrap_what_if.validate(document, existing_resource_id=resource_id)


def test_workspace_verifier_accepts_cli_and_arm_response_shapes() -> None:
    profile = {"hardwareProfile": {"vmSize": "Standard_D2s_v5"}}
    assert verify_workspace.resource_properties(profile) == profile
    assert verify_workspace.resource_properties({"properties": profile}) == profile
    view = {"statuses": [{"code": "PowerState/running"}, {"code": "ProvisioningState/succeeded"}]}
    expected = {"PowerState/running", "ProvisioningState/succeeded"}
    assert verify_workspace.instance_statuses(view) == expected
    assert verify_workspace.instance_statuses({"instanceView": view}) == expected
    assert verify_workspace.instance_statuses({}) == set()


def test_workspace_config_contains_no_generated_cloud_identifier() -> None:
    serialized = json.dumps(workspace_parameters.load_config(CONFIG)).casefold()

    assert "subscription" not in serialized
    assert "tenant" not in serialized
    assert "clientid" not in serialized
    assert "sql-learningnemo-generated" not in serialized


def test_workspace_policies_are_distinct_and_non_root() -> None:
    policies = {
        name: yaml.safe_load((PHASE_DIR / "openshell" / f"{name}-policy.yaml").read_text())
        for name in ("planning", "execution", "probe")
    }
    expected_keys = {"version", "filesystem_policy", "landlock", "process", "network_policies"}
    assert all(set(policy) == expected_keys for policy in policies.values())
    assert all(
        policy["process"] == {"run_as_user": "sandbox", "run_as_group": "sandbox"}
        for policy in policies.values()
    )
    assert set(policies["planning"]["network_policies"]) == {"diagnostic"}
    assert set(policies["execution"]["network_policies"]) == {"remediation"}
    assert policies["probe"]["network_policies"] == {}
    planning_endpoint = policies["planning"]["network_policies"]["diagnostic"]["endpoints"][0]
    execution_endpoint = policies["execution"]["network_policies"]["remediation"]["endpoints"][0]
    assert planning_endpoint["rules"] == [
        {"allow": {"method": "GET", "path": "/v1/diagnostics/current"}}
    ]
    assert execution_endpoint["rules"] == [
        {"allow": {"method": "POST", "path": "/v1/remediations/execute"}}
    ]


def test_bootstrap_denials_execute_the_requested_probe() -> None:
    source = (ROOT / "scripts" / "bootstrap-saw-openshell.sh").read_text(encoding="utf-8")
    template = (PHASE_DIR / "workspace-openshell-bootstrap.bicep").read_text(encoding="utf-8")

    assert '"$package_sha256" "$package_path" | sha256sum -c --quiet' in source
    assert "curl -LsSf" not in source
    assert "curl -fsSL" not in source
    assert '>> "$state_dir/bootstrap.log" 2>&1' in source
    assert 'exec > >(tee -a "$state_dir/bootstrap.log") 2>&1' in source
    assert "FAIL %s line=%s" in source
    assert "FAIL %s line=%s\\n' \"$1\" \"$line\" >&2" in source
    assert "apt-get" not in source
    assert "dpkg --force-confdef --force-confnew -i" in source
    assert "--connect-timeout 10" in source
    assert "--max-time 180" in source
    assert "PASS saw_bootstrap_egress_started" in source
    assert "PASS saw_bootstrap_egress_verified" in source
    assert "set -Eeuo pipefail" in source
    assert "trap 'fail \"$failure_category\" \"$LINENO\"' ERR" in source
    assert '"$admin_home/.local/share/openshell"' in source
    assert "run_as_user = \"10001\"" not in source
    assert "allow_unauthenticated_users = false" in source
    assert "ttl_secs = 900" in source
    assert "grpc_rate_limit_requests = 120" in source
    assert "grpc_rate_limit_window_seconds = 60" in source
    assert "[[ -c /dev/kvm ]]" in source
    assert "--server-san host.containers.internal" in source
    assert 'grpc_endpoint = "https://host.openshell.internal:17670"' in source
    assert "OPENSHELL_VM_SANDBOX_UID" not in source
    assert "OPENSHELL_VM_SANDBOX_GID" not in source
    assert 'guest_tls_cert = "$tls_dir/client/tls.crt"' in source
    assert "openshell gateway list --output json" in source
    assert "openshell gateway select openshell" in source
    assert "gateway_registration" in source
    assert 'value.get("status") == "connected"' in source
    assert 'authentication.get("status") == "authenticated"' in source
    assert "openshell status --output json >/dev/null 2>&1" not in source
    gateway_section = source.split("[openshell.gateway]", 1)[1].split("[openshell.gateway.gateway_jwt]", 1)[0]
    driver_section = source.split("[openshell.drivers.vm]", 1)[1].split("EOF", 1)[0]
    assert 'guest_tls_cert = "$tls_dir/client/tls.crt"' in gateway_section
    assert "guest_tls_cert" not in driver_section
    assert "sandbox_uid" not in driver_section
    assert "sandbox_gid" not in driver_section
    assert 'jwt_dir="$tls_dir/jwt"' in source
    assert "probe_imds_failed" in source
    assert "probe_sql_failed" in source
    assert "probe_external_egress_failed" in source
    assert "probe_privilege_escalation_failed" in source
    assert "openshell sandbox ssh-config" in source
    assert "run_sandbox_script()" in source
    assert 'setpriv --reuid="$admin_uid"' in source
    assert 'ssh -n -F "$config"' in source
    assert "openshell sandbox exec" not in source
    assert "openshell_planning_route_denial_failed" in source
    assert "openshell_execution_route_denial_failed" in source
    create_sandbox = source.split("create_sandbox()", 1)[1].split("failure_category=", 1)[0]
    assert "--output json" not in create_sandbox
    assert "OPENSHELL_PROVISION_TIMEOUT=180" in create_sandbox
    assert 'usermod -aG kvm "$admin_user"' in source
    assert 'chown -R "$admin_user:$admin_user" "$admin_home/.local/state/openshell"' in source
    assert 'loginctl terminate-user "$admin_user"' in source
    assert "UMask=0022" in source
    assert "sed -i \"s/__DIAGNOSTIC_HOST__" not in source
    assert "sed -i \"s/__REMEDIATION_HOST__" not in source
    assert "base64(planningPolicy)" in template
    assert "base64(executionPolicy)" in template
    assert "var planningPolicy = replace(" in template
    assert "var executionPolicy = replace(" in template
    assert 'failure_category="openshell_policy_render_failed"' in source
    assert "grep -Eq '__[A-Z0-9_]+__'" in source


def test_workspace_local_preflight_bounds_dependency_lifetimes(tmp_path: Path) -> None:
    now = dt.datetime.now(dt.UTC)
    parameters = materialize_workspace(tmp_path, now)
    foundation = tmp_path / "foundation.parameters.json"
    foundation_parameters.materialize(
        PHASE_DIR / "environments" / "dev.parameters.json",
        foundation,
        (now + dt.timedelta(days=1)).date().isoformat(),
    )
    dependencies = []
    for index in range(3):
        path = tmp_path / f"dependency-{index}.json"
        path.write_text(json.dumps({"expiresAt": timestamp(now, hours=2)}), encoding="utf-8")
        dependencies.append(path)

    values = preflight_workspace.validate_local(
        CONFIG,
        parameters,
        foundation,
        tuple(dependencies),
        now=now,
    )
    assert values["vmName"] == "vm-learningnemo-saw-dev"

    dependencies[0].write_text(
        json.dumps({"expiresAt": timestamp(now, hours=0)}),
        encoding="utf-8",
    )
    with pytest.raises(preflight_workspace.WorkspacePreflightError, match="outlives"):
        preflight_workspace.validate_local(
            CONFIG,
            parameters,
            foundation,
            tuple(dependencies),
            now=now,
        )


def test_workspace_preflight_reports_the_failed_azure_stage(monkeypatch) -> None:
    class TimedOut:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(preflight_workspace.subprocess, "run", lambda *_args, **_kwargs: TimedOut())

    with pytest.raises(preflight_workspace.WorkspacePreflightError, match="SKU readiness query failed"):
        preflight_workspace.az_json(["vm", "list-skus"], category="workspace SKU")


def test_workspace_what_if_requires_only_four_creates() -> None:
    def change(change_type: str, resource_type: str) -> dict[str, object]:
        return {
            "changeType": change_type,
            "resourceId": f"/subscriptions/example/providers/{resource_type}/example",
            "after": {"type": resource_type},
        }

    document = {
        "changes": [
            change("Create", resource_type)
            for resource_type in validate_workspace_what_if.EXPECTED_CREATES.elements()
        ]
    }
    assert validate_workspace_what_if.validate(document) == []
    document["changes"].append(change("Create", "Microsoft.Network/publicIPAddresses"))
    assert validate_workspace_what_if.validate(document)


def test_workspace_lock_what_if_requires_four_rules() -> None:
    document = {
        "changes": [
            {
                "changeType": "Create",
                "resourceId": "/subscriptions/example/providers/"
                "Microsoft.Network/networkSecurityGroups/securityRules/rule",
                "after": {"type": "Microsoft.Network/networkSecurityGroups/securityRules"},
            }
            for _ in range(4)
        ]
    }
    assert validate_workspace_lock_what_if.validate(document) == []
    ignored_vm = {
        "changeType": "Ignore",
        "resourceId": "/subscriptions/example/providers/Microsoft.Compute/virtualMachines/vm",
        "after": {"type": "Microsoft.Compute/virtualMachines"},
    }
    document["changes"].append(ignored_vm)
    assert validate_workspace_lock_what_if.validate(document) == []
    ignored_vm["changeType"] = "Modify"
    assert validate_workspace_lock_what_if.validate(document)
    document["changes"].pop()
    document["changes"].pop()
    assert validate_workspace_lock_what_if.validate(document)


def test_workspace_bootstrap_what_if_requires_one_run_command() -> None:
    document = {
        "changes": [
            {
                "changeType": "Create",
                "resourceId": "/subscriptions/example/providers/"
                "Microsoft.Compute/virtualMachines/vm/runCommands/bootstrap",
                "after": {"type": "Microsoft.Compute/virtualMachines/runCommands"},
            },
            {
                "changeType": "Ignore",
                "resourceId": "/subscriptions/example/providers/Microsoft.Network/publicIPAddresses/pip",
                "after": {"type": "Microsoft.Network/publicIPAddresses"},
            },
        ]
    }
    assert validate_workspace_bootstrap_what_if.validate(document) == []
    document["changes"].append(
        {
            "changeType": "Delete",
            "resourceId": "/subscriptions/example/providers/Microsoft.Network/publicIPAddresses/pip",
            "before": {"type": "Microsoft.Network/publicIPAddresses"},
        }
    )
    assert validate_workspace_bootstrap_what_if.validate(document)


def test_workspace_egress_what_if_ignores_preserved_compute_only() -> None:
    def change(change_type: str, resource_type: str) -> dict[str, object]:
        return {
            "changeType": change_type,
            "resourceId": f"/subscriptions/example/providers/{resource_type}/example",
            "after": {"type": resource_type},
        }

    document = {
        "changes": [
            change("Create", "Microsoft.Network/publicIPAddresses"),
            change("Create", "Microsoft.Network/natGateways"),
            change("Ignore", "Microsoft.Compute/virtualMachines"),
        ]
    }
    assert validate_workspace_egress_what_if.validate(document, "resources") == []
    document["changes"].append(change("Create", "Microsoft.Compute/virtualMachines"))
    assert validate_workspace_egress_what_if.validate(document, "resources")


def test_workspace_runtime_lock_validator_rejects_compute() -> None:
    template = {
        "resources": [
            {"type": "Microsoft.Compute/virtualMachines"},
        ]
    }
    failures = validate_workspace_runtime_lock.validate(template)
    assert failures
    assert any("prohibited" in failure for failure in failures)


def test_workspace_runtime_evidence_requires_all_denials() -> None:
    readiness = {
        "schemaVersion": 1,
        "status": "ready",
        "openShellVersion": "0.0.116",
        "computeDriver": "microvm",
        "sandboxCount": 3,
        "gatewayAuthentication": "mtls",
        "sandboxJwtTtlSeconds": 900,
        "expiresAt": "2099-01-01T01:00:00Z",
        "policyHashes": {
            "planning": "a" * 64,
            "execution": "b" * 64,
            "probe": "c" * 64,
        },
        "checks": {name: True for name in verify_workspace.EVIDENCE_CHECKS},
    }
    output = "\n".join(
        [*(f"PASS {name}" for name in sorted(verify_workspace.PASS_MARKERS)), "BEGIN_READINESS"]
    )
    output += "\n" + json.dumps(readiness) + "\nEND_READINESS\n"

    assert verify_workspace.parse_runtime_evidence(output, readiness["expiresAt"]) == readiness
    readiness["checks"]["probeImdsDenied"] = False
    bad = output.split("BEGIN_READINESS", 1)[0] + "BEGIN_READINESS\n" + json.dumps(readiness) + "\nEND_READINESS"
    with pytest.raises(verify_workspace.WorkspaceVerificationError, match="denial"):
        verify_workspace.parse_runtime_evidence(bad, readiness["expiresAt"])


def test_workspace_wrappers_are_what_if_first_and_preserve_foundation() -> None:
    deploy = (PHASE_DIR / "deploy-workspace.sh").read_text(encoding="utf-8")
    remove = (PHASE_DIR / "remove-workspace.sh").read_text(encoding="utf-8")

    assert 'mode="what-if"' in deploy
    assert "az deployment group what-if" in deploy
    assert "validate_workspace_what_if.py" in deploy
    assert "validate_workspace_lock_what_if.py" in deploy
    assert "validate_workspace_egress_what_if.py" in deploy
    assert "workspace-bootstrap-egress.bicep" in deploy
    assert "enableBootstrap=true" not in deploy
    assert "workspace-openshell-bootstrap.bicep" in deploy
    assert "openshell_stack" in deploy
    assert "--kind base" in deploy
    assert "--kind bootstrap" in deploy
    assert "private SAW host tooling and temporary bootstrap egress are ready" in deploy
    assert "attachBootstrapEgress=false" in deploy
    assert "bootstrap_started=true" in deploy
    assert "LEARNINGNEMO_AZURE_APPLY" in deploy
    assert "wp5-wp6-saw-openshell" in deploy
    assert "workspace_started=true" in deploy
    assert "remove-workspace.sh" in deploy
    assert "summarize_workspace_failure.py" in deploy
    assert "capture_workspace_diagnostics.py" in deploy
    assert "failed workspace was diagnosed, bootstrap NAT removed, deallocated, and preserved" in deploy
    assert "diagnostic capture failed; workspace and bootstrap egress were left untouched" in deploy
    assert "az stack group delete" in remove
    assert "az vm deallocate" in remove
    assert "verify_foundation.py" in remove
    assert "attachBootstrapEgress=false" in remove
    assert "LEARNINGNEMO_AZURE_DELETE" in remove
    assert "workspace_resources_exist" in remove
    assert "runtime_rules_exist" in remove
    assert "az vm delete" in remove
    assert "az network nic delete" in remove
    assert "az disk delete" in remove
    assert "az identity delete" in remove
    assert "az network nat gateway delete" in remove
    assert "az network public-ip delete" in remove
    assert "az network nsg rule delete" in remove


def test_workspace_diagnostics_require_sanitized_complete_markers() -> None:
    assert "BEGIN_VM_CONSOLES" in capture_workspace_diagnostics.DIAGNOSTIC_SCRIPT
    assert "rootfs-console.log" in capture_workspace_diagnostics.DIAGNOSTIC_SCRIPT
    assert "-maxdepth 6" in capture_workspace_diagnostics.DIAGNOSTIC_SCRIPT
    response = {
        "value": [
            {
                "message": "BEGIN_WORKSPACE_DIAGNOSTICS\n"
                "openshell_package=install ok installed 0.0.116-1\n"
                "ActiveState=failed\n"
                "END_WORKSPACE_DIAGNOSTICS"
            }
        ]
    }

    text = capture_workspace_diagnostics.extract_diagnostic_text(response)
    assert "openshell_package=install ok" in text
    response["value"][0]["message"] = (
        "BEGIN_WORKSPACE_DIAGNOSTICS\nendpoint=https://example.com\nEND_WORKSPACE_DIAGNOSTICS"
    )
    with pytest.raises(capture_workspace_diagnostics.WorkspaceDiagnosticError, match="unsanitized"):
        capture_workspace_diagnostics.extract_diagnostic_text(response)


def test_workspace_diagnostics_reassemble_bounded_verified_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    text = (
        "BEGIN_WORKSPACE_DIAGNOSTICS\n"
        "openshell_package=install ok installed 0.0.116-1\n"
        "ActiveState=failed\n"
        "END_WORKSPACE_DIAGNOSTICS\n"
    )
    archive = gzip.compress(text.encode("utf-8"), mtime=0)
    digest = hashlib.sha256(archive).hexdigest()
    responses = [
        {
            "value": [
                {
                    "message": f"{capture_workspace_diagnostics.MANIFEST_BEGIN}\n"
                    f"bytes={len(archive)}\nsha256={digest}\n"
                    f"{capture_workspace_diagnostics.MANIFEST_END}"
                }
            ]
        }
    ]
    for index, offset in enumerate(
        range(0, len(archive), capture_workspace_diagnostics.DIAGNOSTIC_CHUNK_BYTES)
    ):
        chunk = archive[offset : offset + capture_workspace_diagnostics.DIAGNOSTIC_CHUNK_BYTES]
        encoded = base64.b64encode(chunk).decode("ascii")
        responses.append(
            {
                "value": [
                    {
                        "message": f"{capture_workspace_diagnostics.CHUNK_BEGIN}\n"
                        f"index={index}\ndata={encoded}\n"
                        f"{capture_workspace_diagnostics.CHUNK_END}"
                    }
                ]
            }
        )

    monkeypatch.setattr(
        capture_workspace_diagnostics,
        "invoke_script",
        lambda _resource_group, _vm_name, _script: responses.pop(0),
    )

    assert capture_workspace_diagnostics.retrieve_diagnostic_text("resource-group", "vm") == text.rstrip()
    assert responses == []


def test_workspace_diagnostic_report_validation_ignores_json_syntax() -> None:
    capture_workspace_diagnostics.validate_report(
        {"guestDiagnostics": ["OPENSHELL_SANDBOX_TOKEN="]}
    )
    with pytest.raises(capture_workspace_diagnostics.WorkspaceDiagnosticError, match="unsanitized"):
        capture_workspace_diagnostics.validate_report(
            {"guestDiagnostics": ["OPENSHELL_SANDBOX_TOKEN=example-value"]}
        )


def test_workspace_failure_summary_exposes_only_allowlisted_categories(tmp_path: Path) -> None:
    evidence = {
        "code": "DeploymentFailed",
        "details": [
            "PASS saw_host_prerequisites",
            "PASS openshell_package_verified",
            "FAIL openshell_gateway_readiness_failed endpoint=secret",
            "FAIL openshell_policy_render_failed placeholder=secret",
            "FAIL sensitive_internal_failure",
            {"code": "VMExtensionProvisioningError", "message": "private detail"},
        ],
    }

    passed, failed, codes = summarize_workspace_failure.summarize(evidence)
    assert passed == {"saw_host_prerequisites", "openshell_package_verified"}
    assert failed == {"openshell_gateway_readiness_failed", "openshell_policy_render_failed"}
    assert codes == {"DeploymentFailed", "VMExtensionProvisioningError"}
    output = tmp_path / "failure.json"
    summarize_workspace_failure.write_report(output, passed, failed, codes)
    serialized = output.read_text(encoding="utf-8")
    assert "secret" not in serialized
    assert "private detail" not in serialized
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_workspace_failure_summary_uses_only_status_messages() -> None:
    evidence = {
        "source": "FAIL probe_sql_failed failed to connect to bus",
        "properties": {
            "statusMessage": {
                "error": {
                    "code": "VMExtensionProvisioningError",
                    "message": "Job for openshell-gateway.service failed because the control process exited",
                }
            }
        },
    }

    passed, failed, codes = summarize_workspace_failure.summarize(evidence)
    assert passed == set()
    assert failed == {"openshell_service_start_failed"}
    assert codes == {"VMExtensionProvisioningError"}


def test_workspace_failure_summary_classifies_kvm_permission() -> None:
    evidence = {
        "properties": {
            "statusMessage": {
                "message": "openshell-driver-vm failed to open /dev/kvm: Permission denied"
            }
        }
    }

    _passed, failed, _codes = summarize_workspace_failure.summarize(evidence)
    assert "openshell_kvm_permission_failed" in failed