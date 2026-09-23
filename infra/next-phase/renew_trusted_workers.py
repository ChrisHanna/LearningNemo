#!/usr/bin/env python3
"""Renew only the existing verified worker release, never publish current source."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess

from preflight_platform import validate_budget
from workload_release import load_release


ROOT = Path(__file__).resolve().parents[2]
STATE = Path.home() / ".local/state/learningnemo"


def az(*args):
    result = subprocess.run(["az", *args, "-o", "json", "--only-show-errors"], text=True, capture_output=True, check=True, timeout=600)
    return json.loads(result.stdout) if result.stdout.strip() else None


def verify_expiry_only(document):
    for change in document["changes"]:
        if change["changeType"] in ("Ignore", "NoChange"):
            continue
        if change["changeType"] != "Modify" or change.get("after", {}).get("type", "").lower() != "microsoft.resources/tags":
            raise ValueError("renewal may only modify existing worker expiry")
        if not change.get("delta") or any(item["path"] != "properties.tags.expiresAt" for item in change["delta"]):
            raise ValueError("worker configuration drift detected; renewal rejected")


def write_private(name, document):
    path = STATE / name
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as output:
        json.dump(document, output, indent=2)
    path.chmod(0o600)
    return path


def main():
    if os.environ.get("LEARNINGNEMO_AZURE_APPLY") != "renew-trusted-workers":
        raise ValueError("explicit renew-trusted-workers acknowledgement required")
    account = az("account", "show")
    if account["id"] != os.environ["AZURE_SUBSCRIPTION_ID"]:
        raise ValueError("selected subscription mismatch")
    validate_budget(account, required=True, maximum_amount=50)
    original = json.loads((STATE / "workloads-dev.parameters.json").read_text())
    parameters = deepcopy(original)
    values = {key: entry["value"] for key, entry in parameters["parameters"].items()}
    load_release(STATE / "trusted-runtime-dev.release.json", values["imageReference"], artifacts={
        "sbom": STATE / "trusted-runtime-dev.sbom.json", "vulnerabilityReport": STATE / "trusted-runtime-dev.scan.json",
        "signatureVerification": STATE / "trusted-runtime-dev.signature.json", "approvalSchema": STATE / "trusted-runtime-dev.approval-schema.sql",
    })
    now = datetime.now(UTC)
    dependencies = [az("containerapp", "env", "show", "-g", "rg-learningnemo-platform-dev", "-n", "cae-learningnemo-dev"),
                    az("group", "show", "-n", "rg-learningnemo-data-network-dev"),
                    az("acr", "show", "-g", "rg-learningnemo-artifacts-dev", "-n", values["registryServer"].split(".")[0])]
    if any(resource.get("tags", {}).get("owner") != "learningnemo-portfolio" for resource in dependencies):
        raise ValueError("dependency ownership differs")
    expiry = min(now + timedelta(hours=2), *[datetime.fromisoformat(resource["tags"]["expiresAt"].replace("Z", "+00:00")) for resource in dependencies])
    if expiry < now + timedelta(minutes=30):
        raise ValueError("hosting dependency lease too short for renewal")
    workers = []
    snapshots = {}
    for suffix in ("diagnostic", "query-runner", "remediation", "verifier"):
        app = az("containerapp", "show", "-g", "rg-learningnemo-platform-dev", "-n", f"ca-learningnemo-{suffix}-dev")
        if app["tags"].get("owner") != "learningnemo-portfolio" or app["properties"]["template"]["containers"][0]["image"] != values["imageReference"]:
            raise ValueError("deployed worker is not the recorded verified release")
        workers.append({"name": app["name"], "tags": app["tags"]})
        snapshots[suffix] = {"identity": app["identity"], "template": app["properties"]["template"],
                             "configuration": app["properties"]["configuration"]}
    parameters["parameters"]["expiresAt"]["value"] = expiry.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    path = write_private("workloads-dev.renewal.parameters.json", {"parameters": {
        "workers": {"value": workers}, "expiresAt": parameters["parameters"]["expiresAt"]}})
    template = str(ROOT / "infra/next-phase/worker-lease.bicep")
    preview = az("deployment", "group", "what-if", "-g", "rg-learningnemo-platform-dev", "-n", "learningnemo-worker-renewal-dev",
        "--template-file", template, "--parameters", "@" + str(path), "--result-format", "FullResourcePayloads", "--no-pretty-print")
    write_private("workloads-dev.renewal.what-if.json", preview)
    verify_expiry_only(preview)
    print("PASS existing signed worker image preserved; preview changes expiry tags only", flush=True)
    result = az("deployment", "group", "create", "-g", "rg-learningnemo-platform-dev", "-n", "learningnemo-worker-renewal-dev",
        "--template-file", template, "--parameters", "@" + str(path))
    if result["properties"]["provisioningState"] != "Succeeded":
        raise ValueError("worker renewal deployment failed")
    for suffix in ("diagnostic", "query-runner", "remediation", "verifier"):
        app = az("containerapp", "show", "-g", "rg-learningnemo-platform-dev", "-n", f"ca-learningnemo-{suffix}-dev")
        if datetime.fromisoformat(app["tags"]["expiresAt"].replace("Z", "+00:00")) != expiry.replace(microsecond=0):
            raise ValueError("deployed worker expiry differs")
        current = {"identity": app["identity"], "template": app["properties"]["template"], "configuration": app["properties"]["configuration"]}
        if current != snapshots[suffix]:
            raise ValueError("worker configuration changed during lease renewal")
    write_private("workloads-dev.pre-renewal.parameters.json", original)
    write_private("workloads-dev.parameters.json", parameters)
    print("PASS deployed worker lease renewed without image/authentication changes:", parameters["parameters"]["expiresAt"]["value"], flush=True)


if __name__ == "__main__":
    main()