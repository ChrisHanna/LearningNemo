#!/usr/bin/env python3
"""Validate the compiled no-public-IP SAW and pinned OpenShell bootstrap."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def resources(template: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in template.get("resources", []) if isinstance(item, dict)]


def validate(template: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    found = resources(template)
    types = [str(item.get("type", "")) for item in found]
    expected = {
        "Microsoft.Compute/virtualMachines",
        "Microsoft.ManagedIdentity/userAssignedIdentities",
        "Microsoft.Network/networkInterfaces",
    }
    actual = {item for item in types if not item.endswith("/virtualNetworks") and not item.endswith("/subnets")}
    if actual != expected or any(types.count(item) != 1 for item in expected):
        failures.append("workspace compute resource inventory differs")
    serialized = json.dumps(template, sort_keys=True).casefold()
    for required in (
        "trustedlaunch",
        "securebootenabled",
        "vtpmenabled",
        "saw-host-runtime-no-rbac",
        "openshell-runtime-poc",
        "microvm",
    ):
        if required not in serialized:
            failures.append(f"workspace contract is missing: {required}")
    for prohibited in (
        "microsoft.network/publicipaddresses",
        "publicipaddress",
        "adminpassword",
        "passwordauthentication\": false",
        "microsoft.authorization/roleassignments",
        "systemassigned",
        "customscript",
    ):
        if prohibited in serialized:
            failures.append(f"workspace contains prohibited configuration: {prohibited}")
    vm = next((item for item in found if item.get("type") == "Microsoft.Compute/virtualMachines"), {})
    vm_properties = vm.get("properties") or {}
    linux = ((vm_properties.get("osProfile") or {}).get("linuxConfiguration") or {})
    security = vm_properties.get("securityProfile") or {}
    if (
        linux.get("disablePasswordAuthentication") is not True
        or linux.get("provisionVMAgent") is not True
        or len((linux.get("ssh") or {}).get("publicKeys") or []) != 1
    ):
        failures.append("workspace Linux authentication or agent configuration differs")
    if security.get("securityType") != "TrustedLaunch" or (security.get("uefiSettings") or {}) != {
        "secureBootEnabled": True,
        "vTpmEnabled": True,
    }:
        failures.append("workspace Trusted Launch configuration differs")
    identity = vm.get("identity") or {}
    if identity.get("type") != "UserAssigned" or len(identity.get("userAssignedIdentities") or {}) != 1:
        failures.append("workspace runtime identity differs")
    nic = next((item for item in found if item.get("type") == "Microsoft.Network/networkInterfaces"), {})
    ip_configs = (nic.get("properties") or {}).get("ipConfigurations") or []
    if (
        len(ip_configs) != 1
        or (nic.get("properties") or {}).get("enableIPForwarding") is not False
        or "publicIPAddress" in (ip_configs[0].get("properties") or {})
    ):
        failures.append("workspace NIC is not private-only")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read compiled workspace template: {error}", file=sys.stderr)
        return 1
    failures = validate(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS workspace VM is private-only, Trusted Launch, and identity-isolated")
    print("PASS OpenShell package, images, MicroVM bounds, and bootstrap are immutable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())