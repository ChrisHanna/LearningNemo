#!/usr/bin/env python3
"""Verify live private handoff deployment without impersonating a human user."""

import base64
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import pty
import subprocess
import zlib


STATE = Path.home() / ".local/state/learningnemo"
GROUP = "rg-learningnemo-human-dev"
PULL = "7f951dda-4ed3-4680-a7ca-43fe172d538d"


def az(*args):
    result = subprocess.run(["az", *args, "--output", "json", "--only-show-errors"], capture_output=True, text=True, check=True, timeout=90)
    return json.loads(result.stdout) if result.stdout.strip() else None


def runtime_code(kind, section='all'):
    code = """
import os,httpx
from task_agent.control.mssql_client import MssqlProcedureClient as Client
response=httpx.get('http://127.0.0.1:8080/readyz',timeout=30);assert response.status_code==200
client=Client(server=os.environ['LEARNINGNEMO_SQL_SERVER'],database=os.environ['LEARNINGNEMO_SQL_DATABASE'],client_id=os.environ['AZURE_CLIENT_ID'],application_name='Verify')
with client._driver_connect(client._connection_string) as conn:
    with conn.cursor() as cur:
        cur.execute('SELECT CURRENT_USER');assert cur.fetchone()[0]==__PRINCIPAL__
        for obj,perm in [('control.ResolutionPlans','SELECT'),('control.usp_issue_approval','EXECUTE'),('ops.usp_activate_cycle_safe_query','EXECUTE')]:
            cur.execute("SELECT HAS_PERMS_BY_NAME(?,'OBJECT',?)",obj,perm);assert cur.fetchone()[0] in (None,0)
        cur.execute("SELECT OBJECT_SCHEMA_NAME(major_id)+'.'+OBJECT_NAME(major_id) FROM sys.database_permissions WHERE grantee_principal_id=DATABASE_PRINCIPAL_ID() AND permission_name='EXECUTE'")
        expected={'control.usp_list_human_incidents','control.usp_submit_human_plan','control.usp_record_readonly_analysis','control.usp_get_readonly_analysis','control.usp_propose_readonly_analysis'} if KIND=='incident' else {'control.usp_list_review_plans','control.usp_decide_review_plan'}
        if KIND=='execution':
            expected={'control.usp_claim_human_execution','control.usp_record_human_execution_stage','control.usp_complete_human_execution','control.usp_get_human_execution','control.usp_reconcile_human_broker'}
        assert {row[0] for row in cur.fetchall()}==expected
"""
    workers = """
if KIND=='incident':
    import asyncio
    from azure.identity import ManagedIdentityCredential
    from task_agent.control.database_analysis import DiagnosticReader
    async def check_workers():
        reader=DiagnosticReader(os.environ['LEARNINGNEMO_DIAGNOSTIC_AUDIENCE'],lambda:ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID']))
        result=await reader.read()
        assert isinstance(result.query_run_states,dict)
        with ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID']) as credential:
            token=credential.get_token('api://'+os.environ['LEARNINGNEMO_QUERY_AUDIENCE']+'/.default')
            async with httpx.AsyncClient() as client:
                response=await client.post('https://ca-learningnemo-query-runner-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io/v1/query-runs/cancel',headers={'Authorization':'Bearer '+token.token},json={},timeout=45)
                assert response.status_code==403
    asyncio.run(check_workers())
    print('PASS incident_worker_identity_read_access')
if KIND=='execution':
    import asyncio
    from azure.identity import ManagedIdentityCredential
    from task_agent.control.execution_transport import ManagedExecutionTransport
    async def check_execution():
        with ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID']) as credential:
            async with httpx.AsyncClient() as http:
                transport=ManagedExecutionTransport(broker_origin=os.environ['LEARNINGNEMO_BROKER_ORIGIN'],verifier_origin=os.environ['LEARNINGNEMO_VERIFIER_ORIGIN'],broker_audience=os.environ['LEARNINGNEMO_BROKER_AUDIENCE'],verifier_audience=os.environ['LEARNINGNEMO_VERIFIER_AUDIENCE'],credential=credential,client=http)
                result=await transport._post(transport.verifier_origin,'/v1/verifications/cycle-recovery',transport.verifier_audience,{'safe_query_version':'cycle-safe-v1'})
                assert set(result['checks'])=={'safe_query_version_active','no_owned_query_running','deterministic_result'}
                token=credential.get_token('api://'+transport.broker_audience+'/.default')
                response=await http.post(transport.broker_origin+'/v1/remediations/execute',headers={'Authorization':'Bearer '+token.token},json={},timeout=45)
                assert response.status_code==422
    asyncio.run(check_execution())
    print('PASS execution_worker_access_and_missing_approval_denial')
"""
    code = (code if section in ('all', 'sql') else 'import os,httpx\n') + (workers if section in ('all', 'workers') else '')
    code += f"\nprint('PASS human_runtime_{kind}_{section}')\n"
    code = code.replace("__PRINCIPAL__", repr(f"id-learningnemo-{kind}-dev")).replace("KIND", repr(kind))
    compile(code, '<human-runtime-verification>', 'exec')
    return code


def runtime_check(kind):
    for section in ('sql', 'workers'):
        encoded = base64.b64encode(zlib.compress(runtime_code(kind, section).encode())).decode()
        output = bytearray()
        def capture(descriptor):
            chunk = os.read(descriptor, 4096)
            output.extend(chunk)
            return chunk
        pty.spawn(["az", "containerapp", "exec", "-g", GROUP, "-n", f"ca-learningnemo-{kind}-dev",
            "--command", f"python -c exec(__import__('zlib').decompress(__import__('base64').b64decode('{encoded}')))"], master_read=capture)
        if f"PASS human_runtime_{kind}_{section}".encode() not in output or b"Traceback" in output:
            raise ValueError(f"{kind} {section} runtime verification failed; no success assumed")


def main():
    subscription = os.environ["AZURE_SUBSCRIPTION_ID"]
    if az("account", "show")["id"] != subscription:
        raise ValueError("active subscription differs")
    live = json.loads((STATE / "human-services.live.json").read_text())
    verified = json.loads((STATE / "human-migration.verified.json").read_text())
    if live["subscription"] != subscription or verified["receipt"].get("verifiedAcrossConnections") is not True:
        raise ValueError("independent SQL commit verification required")
    if (STATE / "human-migration.cleanup.json").exists():
        raise ValueError("migration administrator cleanup incomplete")
    jobs = az("containerapp", "job", "list", "-g", GROUP)
    if jobs:
        if len(jobs) != 1 or jobs[0]["name"] != "caj-learningnemo-human-mig-dev" or jobs[0]["tags"].get("purpose") != "retained-disabled-human-migration":
            raise ValueError("unexpected remaining jobs in human-service group")
        job = az("containerapp", "job", "show", "-g", GROUP, "-n", jobs[0]["name"])
        container = job["properties"]["template"]["containers"][0]
        if job.get("identity", {}).get("userAssignedIdentities") or container.get("env") or container.get("command") != ['python', '-c', 'raise SystemExit("retained migration job disabled")']:
            raise ValueError("retained job still has authority")
    resources = az("resource", "list", "-g", GROUP)
    expected = {f"ca-learningnemo-{kind}-dev": "Microsoft.App/containerApps" for kind in ("incident", "review", "execution")}
    expected.update({f"id-learningnemo-{kind}-dev": "Microsoft.ManagedIdentity/userAssignedIdentities" for kind in ("incident", "review", "execution")})
    if jobs:
        expected[jobs[0]["name"]] = "Microsoft.App/jobs"
    if {row["name"]: row["type"] for row in resources} != expected:
        raise ValueError("human-service resource inventory differs")
    results = {}
    for kind in ("incident", "review", "execution"):
        identity = az("identity", "show", "-g", GROUP, "-n", f"id-learningnemo-{kind}-dev")
        if identity["principalId"] != live["principals"][kind]["objectId"] or identity["clientId"] != live["principals"][kind]["clientId"]:
            raise ValueError("service identity differs from SQL binding")
        grants = az("role", "assignment", "list", "--assignee-object-id", identity["principalId"], "--all")
        if len(grants) != 1 or not grants[0]["roleDefinitionId"].endswith(PULL) or grants[0]["scope"].lower() != live["registryId"].lower():
            raise ValueError("service must have only registry-scoped AcrPull in Azure RBAC")
        app = az("containerapp", "show", "-g", GROUP, "-n", f"ca-learningnemo-{kind}-dev")
        properties = app["properties"]
        if properties["latestRevisionName"] != properties["latestReadyRevisionName"] or properties["runningStatus"] != "Running":
            raise ValueError("latest service revision is not ready")
        if properties["configuration"]["ingress"]["external"] or properties["configuration"]["ingress"]["allowInsecure"]:
            raise ValueError("private HTTPS ingress required")
        if properties["template"]["containers"][0]["image"] != live["values"]["image"]:
            raise ValueError("deployed image differs from verified migration release")
        env = {entry["name"]: entry.get("value") for entry in properties["template"]["containers"][0]["env"]}
        expiry = datetime.fromisoformat(env[f"LEARNINGNEMO_{kind.upper()}_EXPIRES_AT"].replace("Z", "+00:00"))
        if expiry <= datetime.now(UTC):
            raise ValueError("service lease expired")
        runtime_check(kind)
        results[kind] = {"revision": properties["latestReadyRevisionName"], "expiresAt": expiry.isoformat(),
            "privateIngress": True, "sqlReadiness": True, "directDataAndExecutionDenied": True}
    admin = az("identity", "show", "-g", "rg-learningnemo-data-dev", "-n", "id-learningnemo-sql-admin-dev")
    if admin["isolationScope"] != "Regional":
        raise ValueError("SQL bootstrap identity isolation not restored")
    result = {"checkedAt": datetime.now(UTC).isoformat(), "services": results, "migration": verified["receipt"],
              "humanRehearsalVerified": False, "sandboxIncidentVerified": False}
    path = STATE / "human-services.verified.json"
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as output:
        json.dump(result, output, indent=2)
    path.chmod(0o600)
    print("PASS live private services, exact identities/grants, SQL readiness, privilege denials, and administrator cleanup")
    print("INFO real-user and full sandbox incident rehearsals remain separate gates")


if __name__ == "__main__":
    main()