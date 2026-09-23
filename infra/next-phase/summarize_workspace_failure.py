#!/usr/bin/env python3
"""Report only allowlisted stages and categories from a failed SAW deployment."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


PASS_CATEGORIES = {
    "saw_host_prerequisites",
    "saw_bootstrap_egress_started",
    "saw_bootstrap_egress_verified",
    "openshell_package_verified",
    "openshell_gateway_authenticated",
    "openshell_three_microvm_sandboxes",
    "openshell_planning_execution_separation",
    "openshell_probe_denials",
    "saw_maximum_lifetime_armed",
    "saw_openshell_ready",
}
FAIL_CATEGORIES = {
    "saw_host_architecture_failed",
    "saw_nested_virtualization_failed",
    "saw_cgroup_v2_failed",
    "saw_host_image_failed",
    "saw_admin_user_failed",
    "saw_expiration_failed",
    "saw_unexpected_bootstrap_failed",
    "saw_package_index_failed",
    "saw_bootstrap_packages_failed",
    "saw_host_tooling_failed",
    "saw_kvm_group_missing",
    "openshell_package_download_failed",
    "openshell_package_hash_failed",
    "openshell_package_install_failed",
    "openshell_package_version_failed",
    "openshell_policy_render_failed",
    "openshell_vm_driver_missing",
    "openshell_gateway_readiness_failed",
    "openshell_sandbox_inventory_failed",
    "openshell_sandbox_verification_transport_failed",
    "openshell_probe_denials_failed",
    "saw_openshell_ready_failed",
    "openshell_planning_policy_failed",
    "openshell_planning_route_denial_failed",
    "openshell_execution_policy_failed",
    "openshell_execution_route_denial_failed",
    "probe_external_egress_failed",
    "probe_imds_failed",
    "probe_sql_failed",
    "probe_host_boundary_failed",
    "probe_policy_write_failed",
    "probe_privilege_escalation_failed",
    "openshell_user_bus_failed",
    "openshell_unit_missing",
    "openshell_unit_enable_failed",
    "openshell_service_start_failed",
    "openshell_service_configuration_failed",
    "openshell_compute_driver_failed",
    "openshell_runtime_permission_failed",
    "openshell_tls_failed",
    "openshell_kvm_permission_failed",
    "openshell_state_permission_failed",
    "openshell_config_permission_failed",
    "openshell_driver_socket_permission_failed",
}
ARM_CODES = {
    "DeploymentFailed",
    "ResourceDeploymentFailure",
    "VMExtensionProvisioningError",
    "OperationPreempted",
    "OperationTimedOut",
}
RESULT_PATTERN = re.compile(r"\b(PASS|FAIL) ([a-z0-9_]+)\b")
INFERRED_FAILURES = {
    "openshell_user_bus_failed": re.compile(
        r"failed to connect to bus|system has not been booted with systemd",
        re.IGNORECASE,
    ),
    "openshell_unit_missing": re.compile(
        r"unit openshell-gateway(?:\.service)? (?:could not be found|not found)",
        re.IGNORECASE,
    ),
    "openshell_unit_enable_failed": re.compile(r"failed to enable unit", re.IGNORECASE),
    "openshell_service_start_failed": re.compile(
        r"job for openshell-gateway(?:\.service)? failed|openshell-gateway\.service: failed|control process exited",
        re.IGNORECASE,
    ),
    "openshell_service_configuration_failed": re.compile(
        r"unknown (?:key|field)|invalid (?:configuration|value)|failed to parse",
        re.IGNORECASE,
    ),
    "openshell_compute_driver_failed": re.compile(
        r"compute driver.*(?:failed|error)|openshell-driver-vm.*(?:failed|error)|libkrun.*(?:failed|error)",
        re.IGNORECASE,
    ),
    "openshell_runtime_permission_failed": re.compile(r"permission denied", re.IGNORECASE),
    "openshell_tls_failed": re.compile(
        r"(?:tls|certificate).*(?:failed|error|invalid)|(?:failed|error|invalid).*(?:tls|certificate)",
        re.IGNORECASE,
    ),
    "openshell_kvm_permission_failed": re.compile(
        r"(?:/dev/kvm|kvm).{0,120}permission denied|permission denied.{0,120}(?:/dev/kvm|kvm)",
        re.IGNORECASE | re.DOTALL,
    ),
    "openshell_state_permission_failed": re.compile(
        r"(?:openshell\.db|\.local/state|state_dir).{0,120}permission denied|permission denied.{0,120}(?:openshell\.db|\.local/state|state_dir)",
        re.IGNORECASE | re.DOTALL,
    ),
    "openshell_config_permission_failed": re.compile(
        r"gateway\.toml.{0,120}permission denied|permission denied.{0,120}gateway\.toml",
        re.IGNORECASE | re.DOTALL,
    ),
    "openshell_driver_socket_permission_failed": re.compile(
        r"(?:compute-driver\.sock|driver socket).{0,120}permission denied|permission denied.{0,120}(?:compute-driver\.sock|driver socket)",
        re.IGNORECASE | re.DOTALL,
    ),
}


class WorkspaceFailureSummaryError(RuntimeError):
    pass


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
        raise WorkspaceFailureSummaryError("workspace failure query failed or timed out") from error
    if result.returncode != 0:
        raise WorkspaceFailureSummaryError("workspace failure query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise WorkspaceFailureSummaryError("workspace failure query returned unreadable JSON") from error


def string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in string_values(child)]
    if isinstance(value, list):
        return [text for child in value for text in string_values(child)]
    return []


def code_values(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in {"code", "statuscode"} and isinstance(child, str) and child in ARM_CODES:
                found.add(child)
            found.update(code_values(child))
    elif isinstance(value, list):
        for child in value:
            found.update(code_values(child))
    return found


def status_message_values(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() == "statusmessage":
                found.extend(string_values(child))
            else:
                found.extend(status_message_values(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(status_message_values(child))
    return found


def summarize(value: Any) -> tuple[set[str], set[str], set[str]]:
    messages = status_message_values(value)
    texts = messages or string_values(value)
    results = {
        (match.group(1), match.group(2))
        for text in texts
        for match in RESULT_PATTERN.finditer(text)
    }
    passed = {category for outcome, category in results if outcome == "PASS" and category in PASS_CATEGORIES}
    failed = {category for outcome, category in results if outcome == "FAIL" and category in FAIL_CATEGORIES}
    combined = "\n".join(texts)
    failed.update(
        category for category, pattern in INFERRED_FAILURES.items() if pattern.search(combined)
    )
    return passed, failed, code_values(value)


def latest_deployment(resource_group: str, prefix: str) -> tuple[str, Any]:
    deployments = az_json(["deployment", "group", "list", "--resource-group", resource_group])
    matching = [
        item
        for item in deployments
        if isinstance(item, dict) and str(item.get("name", "")).startswith(prefix)
    ]
    if not matching:
        raise WorkspaceFailureSummaryError("workspace deployment history is absent")
    deployment = max(
        matching,
        key=lambda item: str((item.get("properties") or {}).get("timestamp", "")),
    )
    name = str(deployment.get("name", ""))
    operations = az_json(
        [
            "deployment",
            "operation",
            "group",
            "list",
            "--resource-group",
            resource_group,
            "--name",
            name,
        ]
    )
    return name, {"deployment": deployment, "operations": operations}


def write_report(output: Path, passed: set[str], failed: set[str], codes: set[str]) -> None:
    report = {
        "schemaVersion": 1,
        "status": "failed",
        "completedStages": sorted(passed),
        "failureCategories": sorted(failed),
        "armCodes": sorted(codes),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--deployment-prefix", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        _name, evidence = latest_deployment(args.resource_group, args.deployment_prefix)
        passed, failed, codes = summarize(evidence)
        if args.output is not None:
            write_report(args.output, passed, failed, codes)
    except WorkspaceFailureSummaryError as error:
        print(f"INFO {error}")
        return 0
    for stage in sorted(passed):
        print(f"INFO workspace bootstrap completed stage: {stage}")
    for category in sorted(failed):
        print(f"INFO workspace bootstrap failure category: {category}")
    for code in sorted(codes):
        print(f"INFO workspace ARM failure code: {code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())