#!/usr/bin/env python3
"""Guarded phased deployment of private human-handoff services."""

import argparse
from datetime import UTC, datetime, timedelta
import json
import hashlib
import os
from pathlib import Path
import subprocess
from uuid import UUID, uuid5

from validate_human_services import validate
from preflight_platform import validate_budget


ROOT = Path(__file__).resolve().parents[2]
STATE = Path.home() / ".local/state/learningnemo"
GROUP = "rg-learningnemo-human-dev"
OWNER = "learningnemo-portfolio"
PULL = "7f951dda-4ed3-4680-a7ca-43fe172d538d"
JOB = "caj-learningnemo-human-mig-dev"


def save(name, value):
    path = STATE / name
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
        json.dump(value, stream, indent=2)
    path.chmod(0o600)
    return path


def az(*args, body=None):
    command = ["az", *args, "--output", "json", "--only-show-errors"]
    if body is not None:
        command += ["--body", "@" + str(save("human-request.private.json", body)), "--headers", "Content-Type=application/json"]
    result = subprocess.run(command, text=True, capture_output=True, timeout=900)
    if result.returncode:
        (STATE / "human-deployment-error.log").write_text(result.stderr)
        (STATE / "human-deployment-error.log").chmod(0o600)
        raise RuntimeError("Azure operation failed; private human-deployment-error.log retained")
    return json.loads(result.stdout.lstrip('\ufeff')) if result.stdout.strip() else None


def parameters(values):
    return {"$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
            "contentVersion": "1.0.0.0", "parameters": {name: {"value": value} for name, value in values.items()}}


def preview_apply(template, path, name, allowed):
    result = az("deployment", "group", "what-if", "-g", GROUP, "-n", name,
        "--template-file", str(template), "--parameters", "@" + str(path), "--no-pretty-print")
    save(f"{name}.what-if.json", result)
    if result.get("status") not in (None, "Succeeded"):
        raise ValueError("deployment preview did not succeed")
    for change in result["changes"]:
        if change["changeType"] in ("Ignore", "NoChange"):
            continue
        if change["changeType"] not in ("Create", "Modify", "Deploy") or change.get("after", {}).get("type") not in allowed:
            raise ValueError("unexpected or destructive human-service preview")
        if f"/resourcegroups/{GROUP}/" not in change["resourceId"].lower():
            raise ValueError("human-service change outside owned group")
    print(f"PASS {name} preview: only intended resources", flush=True)
    result = az("deployment", "group", "create", "-g", GROUP, "-n", name,
        "--template-file", str(template), "--parameters", "@" + str(path))
    save(f"{name}.deployment.json", result)
    if result["properties"]["provisioningState"] != "Succeeded":
        raise ValueError("deployment did not succeed")


def pull_grant(scope, principal, name):
    assignment = str(uuid5(UUID(os.environ["AZURE_SUBSCRIPTION_ID"]), f"{scope}:{name}:human-pull"))
    existing = az("role", "assignment", "list", "--scope", scope, "--assignee", principal)
    if any(item["roleDefinitionId"].endswith(PULL) and item["scope"].lower() == scope.lower() for item in existing):
        return None
    az("role", "assignment", "create", "--name", assignment, "--assignee-object-id", principal,
       "--assignee-principal-type", "ServicePrincipal", "--role", PULL, "--scope", scope)
    return f"{scope}/providers/Microsoft.Authorization/roleAssignments/{assignment}"


def prepare():
    account = az("account", "show")
    if account["id"] != os.environ["AZURE_SUBSCRIPTION_ID"]:
        raise ValueError("subscription mismatch")
    validate_budget(account, required=True, maximum_amount=50)
    env = az("containerapp", "env", "show", "-g", "rg-learningnemo-platform-dev", "-n", "cae-learningnemo-dev")
    registries = az("acr", "list", "-g", "rg-learningnemo-artifacts-dev")
    servers = az("sql", "server", "list", "-g", "rg-learningnemo-data-dev")
    if len(registries) != 1 or len(servers) != 1:
        raise ValueError("ambiguous registry or database")
    registry, server = registries[0], servers[0]
    network = az("group", "show", "-n", "rg-learningnemo-data-network-dev")
    for resource in (env, registry, server, network):
        if resource.get("tags", {}).get("owner") != OWNER:
            raise ValueError("resource ownership mismatch")
    if server.get("publicNetworkAccess") != "Disabled":
        raise ValueError("SQL public access must remain disabled")
    now = datetime.now(UTC)
    expiry = min(now + timedelta(minutes=100), *[datetime.fromisoformat(item["tags"]["expiresAt"].replace("Z", "+00:00")) for item in (env, registry, network)])
    if expiry <= now + timedelta(minutes=35):
        raise ValueError("renew validated dependency leases before rollout")
    settings = json.loads((ROOT / ".nemo-test-client.json").read_text())
    if account["tenantId"] != settings["ENTRA_TENANT_ID"]:
        raise ValueError("tenant mismatch")
    image = (STATE / "cloud-human.image.txt").read_text().strip()
    values = dict(location="eastus", environmentId=env["id"], registryServer=registry["loginServer"], image=image,
        expiresAt=expiry.isoformat(), tenantId=settings["ENTRA_TENANT_ID"], apiClientId=settings["ENTRA_CLIENT_ID"],
        publicClientId=settings["ENTRA_PUBLIC_CLIENT_ID"], sqlServer=server["fullyQualifiedDomainName"], sqlDatabase="learningnemo", deployApps=False)
    worker_receipt = STATE / "incident-workers.verified.json"
    if worker_receipt.exists():
        verified_workers = json.loads(worker_receipt.read_text())
        if verified_workers["subscription"] != account["id"]:
            raise ValueError("incident worker authorization belongs to another subscription")
        values.update(initiationEnabled=False, diagnosticAudience=verified_workers["audiences"]["diagnostic"],
                      queryAudience=verified_workers["audiences"]["query-runner"])
    execution_receipt = STATE / "execution-workers.verified.json"
    if execution_receipt.exists():
        verified_execution = json.loads(execution_receipt.read_text())
        if verified_execution["subscription"] != account["id"]:
            raise ValueError("execution worker subscription mismatch")
        values.update(brokerAudience=verified_execution["audiences"]["remediation"], verifierAudience=verified_execution["audiences"]["verifier"])
    template = az("bicep", "build", "--file", str(ROOT / "infra/next-phase/human-services.bicep"), "--stdout")
    path = save("human-services.parameters.json", parameters(values))
    validate(template, parameters(values), subscription=account["id"], now=now)
    exists = az("group", "exists", "-n", GROUP)
    if exists:
        group = az("group", "show", "-n", GROUP)
        if group.get("tags", {}).get("owner") != OWNER or group["tags"].get("purpose") != "human-handoff":
            raise ValueError("human resource-group ownership collision")
    else:
        az("group", "create", "-n", GROUP, "-l", "eastus", "--tags", f"owner={OWNER}", "project=learningnemo", "purpose=human-handoff", "disposable=true", f"expiresAt={values['expiresAt']}")
    preview_apply(ROOT / "infra/next-phase/human-services.bicep", path, "human-identities", {"Microsoft.ManagedIdentity/userAssignedIdentities"})
    principals = {}
    grants = {}
    for kind in ("incident", "review", "execution"):
        identity = az("identity", "show", "-g", GROUP, "-n", f"id-learningnemo-{kind}-dev")
        principals[kind] = {"clientId": identity["clientId"], "objectId": identity["principalId"]}
        grants[kind] = pull_grant(registry["id"], identity["principalId"], kind)
    save("human-services.live.json", {"subscription": account["id"], "principals": principals, "pullGrants": grants,
        "registryId": registry["id"], "values": values})
    print("PASS private API identities prepared with registry-only grants", flush=True)


def migrate(live, invoice_probe=False, invoice_apply=False, invoice_verify=False):
    admin = az("identity", "show", "-g", "rg-learningnemo-data-dev", "-n", "id-learningnemo-sql-admin-dev")
    record_path = STATE / "human-migration.cleanup.json"
    if record_path.exists():
        raise ValueError("pending migration cleanup must be resolved first")
    record = {"identityId": admin["id"], "isolationScope": admin["isolationScope"], "temporaryPull": None}
    save(record_path.name, record)
    record["temporaryPull"] = pull_grant(live["registryId"], admin["principalId"], "temporary-sql-admin")
    save(record_path.name, record)
    if admin["isolationScope"] != "None":
        az("rest", "--method", "PATCH", "--url", admin["id"] + "?api-version=2024-11-30", body={"properties": {"isolationScope": "None"}})
    values = live["values"]
    expiry = min(datetime.now(UTC) + timedelta(minutes=40), datetime.fromisoformat(values["expiresAt"]))
    migration = dict(location="eastus", environmentId=values["environmentId"], sqlAdminIdentityId=admin["id"], sqlAdminClientId=admin["clientId"],
        registryServer=values["registryServer"], image=values["image"], sqlServer=values["sqlServer"], sqlDatabase=values["sqlDatabase"],
        expiresAt=expiry.isoformat(), principals=live["principals"])
    if invoice_probe or invoice_apply or invoice_verify:
        image = (STATE / 'cloud-human.image.txt').read_text().strip()
        if not image.startswith(values['registryServer'] + '/learningnemo/cloud-human@sha256:'):
            raise ValueError('pinned owned validation image required')
        migration.update(image=image, invoiceProbeOnly=invoice_probe)
        if invoice_apply or invoice_verify:
            invoice=json.loads((STATE/'invoice-services.live.json').read_text())
            migration.update(invoiceApply=invoice_apply,invoiceVerifyOnly=invoice_verify,invoicePrincipals=invoice['principals'])
    path = save("human-migration.parameters.json", parameters(migration))
    preview_apply(ROOT / "infra/next-phase/human-migration-job.bicep", path, "human-migration", {"Microsoft.App/jobs"})
    execution = az("containerapp", "job", "start", "-g", GROUP, "-n", JOB)
    save('invoice-migration.execution.json' if invoice_apply or invoice_verify else 'invoice-sql-probe.execution.json' if invoice_probe else "human-migration.execution.json", execution)
    print("STARTED migration execution: " + execution["name"], flush=True)


def cleanup():
    record = json.loads((STATE / "human-migration.cleanup.json").read_text())
    jobs = az("containerapp", "job", "list", "-g", GROUP)
    job = next((item for item in jobs if item["name"] == JOB), None)
    if job:
        if job["tags"].get("owner") != OWNER:
            raise ValueError("migration job ownership differs")
        if (STATE / "testing-retention.json").exists():
            live = json.loads((STATE / "human-services.live.json").read_text())["values"]
            retained_values = {key: live[key] for key in ("environmentId", "image", "expiresAt")}
            retained_values['image'] = job['properties']['template']['containers'][0]['image']
            path = save("human-retained-job.parameters.json", parameters(retained_values))
            preview_apply(ROOT / "infra/next-phase/human-retained-job.bicep", path, "human-retained-job", {"Microsoft.App/jobs"})
            retained = az("containerapp", "job", "show", "-g", GROUP, "-n", JOB)
            if retained.get("identity", {}).get("type") not in (None, "None") or retained.get("identity", {}).get("userAssignedIdentities"):
                raise ValueError("retained migration identity was not detached")
        else:
            az("containerapp", "job", "delete", "-g", GROUP, "-n", JOB, "--yes")
    if record["temporaryPull"]:
        az("role", "assignment", "delete", "--ids", record["temporaryPull"])
    az("rest", "--method", "PATCH", "--url", record["identityId"] + "?api-version=2024-11-30", body={"properties": {"isolationScope": record["isolationScope"]}})
    identity = az("identity", "show", "--ids", record["identityId"])
    if identity["isolationScope"] != record["isolationScope"]:
        raise ValueError("SQL identity isolation restoration failed")
    save("human-migration.cleanup-completed.json", record)
    (STATE / "human-migration.cleanup.json").unlink()
    print("PASS migration authority removed; job retained when requested; SQL identity isolation restored", flush=True)


def verify_migration(live):
    execution = json.loads((STATE / "human-migration.execution.json").read_text())["name"]
    executions = az("containerapp", "job", "execution", "list", "-g", GROUP, "-n", JOB)
    current = next(item for item in executions if item["name"] == execution)
    if current["properties"]["status"] != "Succeeded":
        raise ValueError("migration execution is not successful: " + current["properties"]["status"])
    logs = subprocess.run(["az", "containerapp", "job", "logs", "show", "-g", GROUP, "-n", JOB,
        "--execution", execution, "--container", "migrator", "--tail", "100", "--format", "json"], capture_output=True, text=True, timeout=90, check=True)
    path = STATE / "human-migration.logs.jsonl"
    path.write_text(logs.stdout)
    path.chmod(0o600)
    receipts = []
    for line in logs.stdout.splitlines():
        try:
            message = json.loads(line).get("Log", "")
        except ValueError:
            message = line
        if "PASS human_migration " in message:
            receipt, _ = json.JSONDecoder().raw_decode(message.split("PASS human_migration ", 1)[1])
            receipts.append(receipt)
    if len(receipts) != 1:
        raise ValueError("exact migration result receipt not found")
    receipt = receipts[0]
    expected = {f"human-{name}": hashlib.sha256((ROOT / "infra/next-phase/review-service" / name).read_text().encode()).hexdigest()
                for name in ("001_review_boundary.sql", "002_incident_handoff.sql", "003_execution_coordination.sql", "004_incident_initiation.sql", "005_execution_status.sql", "006_execution_reconciliation.sql", "007_readonly_analysis.sql")}
    if receipt.get("status") != "verified" or receipt.get("verifiedAcrossConnections") is not True or receipt.get("migrations") != expected or receipt.get("originalReceiptsPreserved", 0) < 6 or receipt.get("principals") != ["execution", "incident", "review"]:
        raise ValueError("migration result does not match this release")
    save("human-migration.verified.json", {"execution": execution, "image": live["values"]["image"], "principals": live["principals"], "receipt": receipt})
    print("PASS real SQL additive receipts and exact API procedure grants verified; original migration receipts preserved", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "migrate", "probe-invoices", "install-invoices", "readback-invoices", "verify-migration", "services", "cleanup-migration"])
    args = parser.parse_args()
    if os.environ.get("LEARNINGNEMO_AZURE_APPLY") != "human-handoff":
        raise ValueError("explicit human-handoff apply acknowledgement required")
    if az("account", "show")["id"] != os.environ["AZURE_SUBSCRIPTION_ID"]:
        raise ValueError("selected subscription mismatch")
    if args.phase == "prepare":
        prepare()
    elif args.phase == "cleanup-migration":
        cleanup()
    else:
        live = json.loads((STATE / "human-services.live.json").read_text())
        if live["subscription"] != os.environ["AZURE_SUBSCRIPTION_ID"]:
            raise ValueError("rollout subscription mismatch")
        if args.phase in ('install-invoices', 'readback-invoices'):
            invoice = json.loads((STATE / 'invoice-services.live.json').read_text())
            if invoice['subscription'] != live['subscription']:
                raise ValueError('invoice rollout subscription mismatch')
            live['values']['expiresAt'] = (datetime.now(UTC) + timedelta(minutes=40)).isoformat() if invoice['values'].get('availabilityMode') == 'operator-managed' and not invoice['values']['expiresAt'] else invoice['values']['expiresAt']
        if datetime.fromisoformat(live["values"]["expiresAt"]) <= datetime.now(UTC) + timedelta(minutes=10):
            raise ValueError("rollout lease too short")
        if args.phase in ("migrate", "probe-invoices", "install-invoices", "readback-invoices"):
            migrate(live, invoice_probe=args.phase == 'probe-invoices', invoice_apply=args.phase == 'install-invoices', invoice_verify=args.phase == 'readback-invoices')
        elif args.phase == "verify-migration":
            verify_migration(live)
        else:
            if not (STATE / "human-migration.verified.json").exists() or (STATE / "human-migration.cleanup.json").exists():
                raise ValueError("verified migration and completed administrator cleanup required")
            verified = json.loads((STATE / "human-migration.verified.json").read_text())
            if verified["image"] != live["values"]["image"] or verified["principals"] != live["principals"]:
                raise ValueError("verified migration belongs to another release or identity set")
            values = {**live["values"], "deployApps": True}
            if not all(values.get(key) for key in ("brokerAudience", "verifierAudience")):
                raise ValueError("verified execution worker authorization required before service rollout")
            path = save("human-services.parameters.json", parameters(values))
            preview_apply(ROOT / "infra/next-phase/human-services.bicep", path, "human-services", {"Microsoft.ManagedIdentity/userAssignedIdentities", "Microsoft.App/containerApps"})
            print("PASS private services deployed; readiness and SQL permissions still require verification", flush=True)


if __name__ == "__main__":
    main()