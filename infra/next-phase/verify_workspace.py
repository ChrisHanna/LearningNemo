#!/usr/bin/env python3
"""Verify the live SAW host, OpenShell gateway, sandboxes, and denial evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from workspace_parameters import WorkspaceParameterError
from workspace_parameters import parameter_values


BASE_TYPES = {
    "microsoft.network/networksecuritygroups": 1,
    "microsoft.network/routetables": 1,
    "microsoft.network/virtualnetworks": 1,
}
WORKSPACE_TYPES = {
    "microsoft.compute/disks": 1,
    "microsoft.compute/virtualmachines": 1,
    "microsoft.managedidentity/userassignedidentities": 1,
    "microsoft.network/networkinterfaces": 1,
}
RUNTIME_RULES = {
    "deny-all-inbound",
    "deny-trusted-platform-outbound",
    "deny-saw-east-west",
    "deny-direct-azure-sql",
    "deny-sandbox-host-imds",
    "allow-azure-platform-dns",
    "allow-approved-azure-https",
    "deny-general-internet-runtime",
}
EVIDENCE_FIELDS = {
    "schemaVersion",
    "status",
    "openShellVersion",
    "computeDriver",
    "sandboxCount",
    "gatewayAuthentication",
    "sandboxJwtTtlSeconds",
    "expiresAt",
    "policyHashes",
    "checks",
}
EVIDENCE_CHECKS = {
    "noPublicIp",
    "nestedVirtualization",
    "planningDiagnosticOnly",
    "executionBrokerRouteOnly",
    "probeExternalEgressDenied",
    "probeImdsDenied",
    "probeSqlDenied",
    "probeHostBoundaryDenied",
    "probePolicyWriteDenied",
    "probePrivilegeEscalationDenied",
}
PASS_MARKERS = {
    "workspace_gateway_authenticated",
    "workspace_sandbox_inventory",
    "workspace_expiry_timer",
    "workspace_host_internet_denied",
    "workspace_host_imds_denied",
}
READINESS_PATTERN = re.compile(r"BEGIN_READINESS\s*(\{.*?\})\s*END_READINESS", re.DOTALL)
UUID_SEARCH = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)


class WorkspaceVerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkspaceVerificationError(message)


def az_json(arguments: list[str], *, timeout: int = 120) -> Any:
    try:
        result = subprocess.run(
            ["az", *arguments, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkspaceVerificationError("workspace verification query failed or timed out") from error
    if result.returncode != 0:
        raise WorkspaceVerificationError("workspace verification query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise WorkspaceVerificationError("workspace verification returned unreadable JSON") from error


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def recursive_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in recursive_strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in recursive_strings(child)]
    return []


def parse_runtime_evidence(response: Any, expected_expiry: str) -> dict[str, Any]:
    text = "\n".join(recursive_strings(response))
    markers = {
        match.group(1)
        for match in re.finditer(r"\bPASS ([a-z0-9_]+)\b", text)
        if match.group(1) in PASS_MARKERS
    }
    require(markers == PASS_MARKERS, "workspace runtime verification markers differ")
    match = READINESS_PATTERN.search(text)
    require(match is not None, "workspace readiness evidence is absent")
    try:
        evidence = json.loads(match.group(1))
    except json.JSONDecodeError as error:
        raise WorkspaceVerificationError("workspace readiness evidence is invalid") from error
    require(isinstance(evidence, dict) and set(evidence) == EVIDENCE_FIELDS, "workspace readiness fields differ")
    require(
        evidence.get("schemaVersion") == 1
        and evidence.get("status") == "ready"
        and evidence.get("openShellVersion") == "0.0.116"
        and evidence.get("computeDriver") == "microvm"
        and evidence.get("sandboxCount") == 3
        and evidence.get("gatewayAuthentication") == "mtls"
        and evidence.get("sandboxJwtTtlSeconds") == 900
        and evidence.get("expiresAt") == expected_expiry,
        "workspace readiness values differ",
    )
    hashes = evidence.get("policyHashes")
    require(
        isinstance(hashes, dict)
        and set(hashes) == {"planning", "execution", "probe"}
        and all(re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in hashes.values())
        and len(set(hashes.values())) == 3,
        "workspace policy hashes differ",
    )
    checks = evidence.get("checks")
    require(
        isinstance(checks, dict)
        and set(checks) == EVIDENCE_CHECKS
        and all(value is True for value in checks.values()),
        "workspace denial evidence differs",
    )
    return evidence


def runtime_probe_script(admin_username: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
uid_value=$(id -u {admin_username})
home_value=$(getent passwd {admin_username} | cut -d: -f6)
runtime_dir=/run/user/$uid_value
run_user() {{
  runuser -u {admin_username} -- env HOME=$home_value XDG_RUNTIME_DIR=$runtime_dir \
    DBUS_SESSION_BUS_ADDRESS=unix:path=$runtime_dir/bus "$@"
}}
run_user openshell --gateway openshell status --output json | python3 -c '
import json, sys
value = json.load(sys.stdin)
raise SystemExit(0 if (
    isinstance(value, dict)
    and value.get("gateway") == "openshell"
    and value.get("server") == "https://127.0.0.1:17670"
    and value.get("status") == "connected"
    and isinstance(value.get("authentication"), dict)
    and value["authentication"].get("status") == "authenticated"
) else 1)
'
echo PASS workspace_gateway_authenticated
run_user openshell sandbox list --output json >/tmp/learningnemo-sandboxes.json
python3 - <<'PY'
import json
value = json.load(open("/tmp/learningnemo-sandboxes.json", encoding="utf-8"))
items = value if isinstance(value, list) else value.get("sandboxes") or value.get("items") or []
raise SystemExit(0 if isinstance(items, list) and len(items) == 3 else 1)
PY
echo PASS workspace_sandbox_inventory
systemctl is-active --quiet learningnemo-saw-expire.timer
systemctl is-enabled --quiet learningnemo-saw-expire.timer
echo PASS workspace_expiry_timer
if curl -fsS --connect-timeout 5 --max-time 8 https://example.com/ >/dev/null 2>&1; then exit 41; fi
echo PASS workspace_host_internet_denied
if curl --noproxy '*' -fsS --connect-timeout 3 --max-time 5 -H Metadata:true \
  'http://169.254.169.254/metadata/instance?api-version=2025-04-07' >/dev/null 2>&1; then exit 42; fi
echo PASS workspace_host_imds_denied
echo BEGIN_READINESS
cat /var/lib/learningnemo-saw/readiness.json
echo END_READINESS
"""


def resource_properties(document: dict[str, Any]) -> dict[str, Any]:
    return document.get("properties") or document


def instance_statuses(document: dict[str, Any]) -> set[str]:
    view = document.get("instanceView") or document
    return {str(item.get("code", "")) for item in view.get("statuses") or []}


def workspace_tags(values: dict[str, Any]) -> dict[str, str]:
    return {
        "project": values["projectName"],
        "environment": values["environment"],
        "managedBy": "bicep-deployment-stack",
        "owner": values["ownerTag"],
        "costProfile": "ephemeral-saw-openshell-poc",
        "trustZone": "untrusted-agent-workspace",
        "platformPhase": "wp5-wp6-saw-openshell",
        "disposable": "true",
        "expiresAt": values["expiresAt"],
        "sawMaturity": "openshell-runtime-poc",
        "openShellVersion": values["openShellVersion"],
        "openShellDriver": "microvm",
        **values["additionalTags"],
    }


def tags_match(actual: Any, expected: dict[str, str]) -> bool:
    return isinstance(actual, dict) and all(actual.get(key) == value for key, value in expected.items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--workspace-stack-name", required=True)
    parser.add_argument("--bootstrap-stack-name", required=True)
    parser.add_argument("--lock-stack-name", required=True)
    parser.add_argument("--workspace-template", type=Path, required=True)
    parser.add_argument("--bootstrap-template", type=Path, required=True)
    parser.add_argument("--lock-template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        try:
            values = parameter_values(args.parameters)
        except WorkspaceParameterError as error:
            raise WorkspaceVerificationError(str(error)) from error
        account = az_json(["account", "show"], timeout=20)
        expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
        require(not expected_subscription or account.get("id") == expected_subscription, "active subscription differs")
        for stack_name in (
            args.workspace_stack_name,
            args.bootstrap_stack_name,
            args.lock_stack_name,
        ):
            stack = az_json(
                [
                    "stack",
                    "group",
                    "show",
                    "--resource-group",
                    values["sawResourceGroupName"],
                    "--name",
                    stack_name,
                ],
                timeout=45,
            )
            state = stack.get("provisioningState") or (stack.get("properties") or {}).get("provisioningState")
            require(str(state).casefold() == "succeeded", "workspace deployment stack is not ready")
        resource_group = values["sawResourceGroupName"]
        resources = az_json(["resource", "list", "--resource-group", resource_group], timeout=45)
        counts: dict[str, int] = {}
        for resource in resources:
            resource_type = str(resource.get("type", "")).casefold()
            counts[resource_type] = counts.get(resource_type, 0) + 1
        for resource_type, count in {**BASE_TYPES, **WORKSPACE_TYPES}.items():
            require(counts.get(resource_type) == count, f"workspace resource count differs: {resource_type}")
        allowed = set(BASE_TYPES) | set(WORKSPACE_TYPES) | {"microsoft.compute/virtualmachines/runcommands"}
        require(set(counts) <= allowed, "workspace resource inventory contains an unapproved type")
        require(counts.get("microsoft.network/publicipaddresses", 0) == 0, "workspace has a public IP resource")
        expected_tags = workspace_tags(values)
        runtime_identity_name = f"id-{values['projectName']}-saw-runtime-{values['environment']}"
        runtime_identity = az_json(
            ["identity", "show", "--resource-group", resource_group, "--name", runtime_identity_name],
            timeout=30,
        )
        require(
            runtime_identity.get("isolationScope") == "Regional"
            and tags_match(
                runtime_identity.get("tags"),
                {**expected_tags, "identityPurpose": "saw-host-runtime-no-rbac"},
            ),
            "workspace runtime identity isolation or tags differ",
        )
        assignments = az_json(
            ["role", "assignment", "list", "--assignee-object-id", runtime_identity["principalId"], "--all"],
            timeout=60,
        )
        require(assignments == [], "workspace runtime identity has an Azure role assignment")
        nic_name = f"nic-{values['vmName']}"
        nic = az_json(
            ["network", "nic", "show", "--resource-group", resource_group, "--name", nic_name],
            timeout=30,
        )
        ip_configs = nic.get("ipConfigurations") or []
        require(
            len(ip_configs) == 1
            and not ip_configs[0].get("publicIPAddress")
            and nic.get("enableIPForwarding") is False
            and tags_match(nic.get("tags"), expected_tags),
            "workspace NIC or public-address binding differs",
        )
        expected_subnet_suffix = (
            f"/virtualNetworks/{values['sawVnetName']}/subnets/{values['workspaceSubnetName']}"
        ).casefold()
        require(
            str((ip_configs[0].get("subnet") or {}).get("id", "")).casefold().endswith(expected_subnet_suffix),
            "workspace NIC subnet differs",
        )
        vm = az_json(["vm", "show", "--resource-group", resource_group, "--name", values["vmName"]], timeout=45)
        vm_properties = resource_properties(vm)
        image = (vm_properties.get("storageProfile") or {}).get("imageReference") or {}
        security = vm_properties.get("securityProfile") or {}
        require(
            (vm_properties.get("hardwareProfile") or {}).get("vmSize") == values["vmSize"]
            and image.get("publisher") == values["imagePublisher"]
            and image.get("offer") == values["imageOffer"]
            and image.get("sku") == values["imageSku"]
            and image.get("version") == values["imageVersion"]
            and security.get("securityType") == "TrustedLaunch"
            and (security.get("uefiSettings") or {}).get("secureBootEnabled") is True
            and (security.get("uefiSettings") or {}).get("vTpmEnabled") is True
            and tags_match(vm.get("tags"), expected_tags),
            "workspace VM image, size, security, or tags differ",
        )
        vm_identities = (vm.get("identity") or {}).get("userAssignedIdentities") or {}
        require(
            (vm.get("identity") or {}).get("type") == "UserAssigned"
            and {str(key).casefold() for key in vm_identities} == {str(runtime_identity["id"]).casefold()},
            "workspace VM identity differs",
        )
        instance = az_json(
            ["vm", "get-instance-view", "--resource-group", resource_group, "--name", values["vmName"]],
            timeout=60,
        )
        statuses = instance_statuses(instance)
        require("PowerState/running" in statuses and "ProvisioningState/succeeded" in statuses, "workspace VM is not running")
        nsg_name = f"nsg-{values['sawVnetName']}-workspace"
        nsg = az_json(["network", "nsg", "show", "--resource-group", resource_group, "--name", nsg_name], timeout=30)
        require(
            {str(item.get("name", "")) for item in nsg.get("securityRules") or []} == RUNTIME_RULES,
            "workspace runtime NSG rules differ",
        )
        vnet = az_json(
            ["network", "vnet", "show", "--resource-group", resource_group, "--name", values["sawVnetName"]],
            timeout=30,
        )
        workspace_subnets = [
            item for item in vnet.get("subnets") or [] if item.get("name") == values["workspaceSubnetName"]
        ]
        require(
            len(workspace_subnets) == 1 and not workspace_subnets[0].get("natGateway"),
            "temporary bootstrap NAT remains attached to the workspace subnet",
        )
        probe = az_json(
            [
                "vm",
                "run-command",
                "invoke",
                "--resource-group",
                resource_group,
                "--name",
                values["vmName"],
                "--command-id",
                "RunShellScript",
                "--scripts",
                runtime_probe_script(values["adminUsername"]),
            ],
            timeout=240,
        )
        readiness = parse_runtime_evidence(probe, values["expiresAt"])
        owner_name = str((account.get("user") or {}).get("name", ""))
        require(owner_name != "", "workspace owner subject is unavailable")
        record = {
            "schemaVersion": 1,
            "status": "ready",
            "verifiedAt": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
            "expiresAt": values["expiresAt"],
            "ownerSubjectHash": hashlib.sha256(
                f"azure-cli-workspace-owner:{owner_name}".encode("utf-8")
            ).hexdigest(),
            "host": {
                "vmSize": values["vmSize"],
                "image": {
                    "publisher": values["imagePublisher"],
                    "offer": values["imageOffer"],
                    "sku": values["imageSku"],
                    "version": values["imageVersion"],
                },
                "trustedLaunch": True,
                "publicIp": False,
                "runtimeIdentityRoleAssignments": 0,
            },
            "openShell": readiness,
            "runtimeLock": {
                "generalInternetDenied": True,
                "imdsDenied": True,
                "azureHttpsOuterAllow": True,
                "openShellInnerPolicyRequired": True,
            },
            "evidenceSha256": {
                "workspaceTemplate": sha256(args.workspace_template),
                "bootstrapTemplate": sha256(args.bootstrap_template),
                "runtimeLockTemplate": sha256(args.lock_template),
                "privateParameters": sha256(args.parameters),
            },
        }
        serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
        require(UUID_SEARCH.search(serialized) is None, "workspace evidence contains an Azure identifier")
        require("/subscriptions/" not in serialized.casefold(), "workspace evidence contains an Azure resource ID")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(args.output)
        print("PASS SAW VM has no public IP, uses Trusted Launch, and its runtime identity has no RBAC")
        print("PASS OpenShell mTLS gateway has exactly three policy-distinct MicroVM sandboxes")
        print("PASS SQL, IMDS, host files, policy writes, external egress, and wrong broker route are denied")
        print("PASS maximum lifetime and post-bootstrap runtime egress lock are active")
        print("PASS wrote owner-bound identifier-free workspace evidence")
        return 0
    except (OSError, WorkspaceVerificationError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())