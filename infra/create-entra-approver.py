"""Provision one dedicated demo Approver without modifying Reader/Operator grants."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
GRAPH = "https://graph.microsoft.com/v1.0"
ROLE = "Task.Approver"
ALIAS = "learningnemo-approver"
STATE_NAME = "approver-dev.identity.json"
CREDENTIAL_NAME = "approver-dev.temporary-credential.json"


class ProvisionError(RuntimeError):
    pass


def az(arguments: list[str], body: dict | None = None):
    with tempfile.TemporaryDirectory(prefix="learningnemo-approver-") as folder:
        command = ["az", *arguments, "--output", "json", "--only-show-errors"]
        if body is not None:
            path = Path(folder) / "request.json"
            write_private(path, body)
            command.extend(["--body", f"@{path}", "--headers", "Content-Type=application/json"])
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ProvisionError("Azure operation unavailable or timed out; inspect state before retrying") from error
        if result.returncode:
            match = re.search(r'"code"\s*:\s*"([A-Za-z0-9_.-]+)"', result.stderr)
            category = match.group(1) if match else "operation_failed"
            raise ProvisionError(f"Azure/Graph request failed ({category}); raw output withheld to protect credentials")
        try:
            return json.loads(result.stdout) if result.stdout.strip() else None
        except json.JSONDecodeError as error:
            raise ProvisionError("Azure returned an invalid response; no success assumed") from error


def graph(method: str, resource: str, body: dict | None = None):
    return az(["rest", "--method", method, "--uri", f"{GRAPH}/{resource}"], body)


def write_private(path: Path, value: dict, *, exclusive: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, indent=2)
        stream.write("\n")


def merged_roles(existing: list[dict], app_id: str) -> tuple[list[dict], str, bool]:
    matches = [role for role in existing if role.get("value") == ROLE]
    if matches:
        if len(matches) != 1 or matches[0].get("isEnabled") is not True or matches[0].get("allowedMemberTypes") != ["User"]:
            raise ProvisionError("Existing Approver role differs; refusing to alter its contract")
        return list(existing), matches[0]["id"], False
    role_id = str(uuid.uuid5(uuid.UUID(app_id), f"app-role:{ROLE}"))
    if any(role.get("id") == role_id for role in existing):
        raise ProvisionError("Approver role ID collides with an existing role")
    return [*existing, {
        "allowedMemberTypes": ["User"],
        "description": "Can approve or reject an exact LearningNeMo remediation plan; cannot execute it.",
        "displayName": "Task Approver", "id": role_id, "isEnabled": True, "value": ROLE,
    }], role_id, True


def user_payload(upn: str, password: str) -> dict:
    return {
        "accountEnabled": True,
        "displayName": "LearningNeMo Approver",
        "mailNickname": ALIAS,
        "userPrincipalName": upn,
        "passwordProfile": {"forceChangePasswordNextSignIn": True, "password": password},
    }


def find_users(upn: str) -> list[dict]:
    query = urlencode({
        "$filter": f"userPrincipalName eq '{upn}'",
        "$select": "id,userPrincipalName,displayName,accountEnabled",
    })
    response = graph("GET", f"users?{query}")
    if response.get("@odata.nextLink"):
        raise ProvisionError("Unexpected paginated user lookup")
    return response["value"]


def assignment_needed(assignments: list[dict], service_principal: str, role_id: str) -> bool:
    target = [item for item in assignments if item.get("resourceId") == service_principal]
    if any(item.get("appRoleId") != role_id for item in target):
        raise ProvisionError("Approver has other LearningNeMo roles; refusing to add, remove, or escalate permissions")
    return not target


def read_private(path: Path) -> dict:
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ProvisionError("Approver private state must be owner-only")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--settings", type=Path, default=ROOT / ".nemo-test-client.json")
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".local/state/learningnemo")
    args = parser.parse_args()
    try:
        settings = json.loads(args.settings.read_text(encoding="utf-8"))
        account = az(["account", "show"])
        if account.get("tenantId") != settings["ENTRA_TENANT_ID"] or account.get("id") != os.getenv("AZURE_SUBSCRIPTION_ID"):
            raise ProvisionError("Pin AZURE_SUBSCRIPTION_ID and sign into the configured Entra tenant")
        domains = graph("GET", "domains?$select=id,isVerified,isInitial,authenticationType")
        initial = [item for item in domains["value"] if item.get("isVerified") and item.get("isInitial") and item.get("authenticationType") == "Managed"]
        if len(initial) != 1 or not re.fullmatch(r"[a-zA-Z0-9.-]+\.onmicrosoft\.com", initial[0]["id"]):
            raise ProvisionError("Expected one verified managed initial tenant domain")
        upn = f"{ALIAS}@{initial[0]['id']}"
        app = az(["ad", "app", "show", "--id", settings["ENTRA_CLIENT_ID"]])
        service = az(["ad", "sp", "show", "--id", settings["ENTRA_CLIENT_ID"]])
        roles, role_id, add_role = merged_roles(app.get("appRoles", []), settings["ENTRA_CLIENT_ID"])
        users = find_users(upn)
        if len(users) > 1:
            raise ProvisionError("Ambiguous Approver user lookup")
        state_dir = args.state_dir.expanduser().resolve()
        if state_dir.is_relative_to(ROOT):
            raise ProvisionError("Private state and credentials must be outside the repository")
        record_path = state_dir / STATE_NAME
        credential_path = state_dir / CREDENTIAL_NAME
        record = read_private(record_path) if record_path.exists() else None
        user = users[0] if users else None
        if user and (not record or record.get("userId") != user["id"] or record.get("tenantId") != account["tenantId"] or record.get("apiClientId") != settings["ENTRA_CLIENT_ID"]):
            raise ProvisionError("Username exists without matching ownership evidence; refusing to adopt or reset it")
        if user and user.get("accountEnabled") is not True:
            raise ProvisionError("Existing Approver is disabled; refusing to enable it implicitly")
        if not user and (record_path.exists() or credential_path.exists()):
            raise ProvisionError("Partial or stale provisioning state exists; inspect it before creating another account")
        assignments = graph("GET", f"users/{user['id']}/appRoleAssignments") if user else {"value": []}
        if assignments.get("@odata.nextLink"):
            raise ProvisionError("Unexpected paginated role inventory; review existing account access")
        add_assignment = assignment_needed(assignments["value"], service["id"], role_id)
        print(f"Approver username: {upn}")
        print(f"Plan: create role={add_role}, create user={user is None}, add Approver assignment={add_assignment}")
        print("Task.Approver only; no Task.Reader, Task.Operator, directory role, or Azure RBAC grant")
        print("Existing users, client scopes, and role assignments remain unchanged")
        if not args.apply:
            print("Preview only. Apply requires LEARNINGNEMO_AZURE_APPLY=approver-identity")
            return 0
        if os.getenv("LEARNINGNEMO_AZURE_APPLY") != "approver-identity":
            raise ProvisionError("Apply requires LEARNINGNEMO_AZURE_APPLY=approver-identity")
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if add_role:
            graph("PATCH", f"applications/{app['id']}", {"appRoles": roles})
        if not user:
            password = "aA7!" + secrets.token_urlsafe(36)
            write_private(credential_path, {
                "userPrincipalName": upn, "temporaryPassword": password,
                "forceChangePasswordNextSignIn": True,
            }, exclusive=True)
            user = graph("POST", "users", user_payload(upn, password))
            record = {
                "schemaVersion": 1, "tenantId": account["tenantId"], "apiClientId": settings["ENTRA_CLIENT_ID"],
                "userId": user["id"], "userPrincipalName": upn, "roleId": role_id,
                "createdAt": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "status": "created-awaiting-assignment",
            }
            write_private(record_path, record)
            del password
        if add_assignment:
            graph("POST", f"users/{user['id']}/appRoleAssignments", {
                "principalId": user["id"], "resourceId": service["id"], "appRoleId": role_id,
            })
        verified = graph("GET", f"users/{user['id']}/appRoleAssignments")
        if verified.get("@odata.nextLink") or assignment_needed(verified["value"], service["id"], role_id):
            raise ProvisionError("Approver role assignment was not verified")
        current = az(["ad", "app", "show", "--id", settings["ENTRA_CLIENT_ID"]])
        verified_roles, verified_id, missing = merged_roles(current.get("appRoles", []), settings["ENTRA_CLIENT_ID"])
        if missing or verified_id != role_id or any(role not in verified_roles for role in app.get("appRoles", [])):
            raise ProvisionError("Role preservation verification failed")
        record["status"] = "approver-assigned"
        record["verifiedAt"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        write_private(record_path, record)
        print("PASS dedicated Approver created/verified; no execution role assigned")
        print(f"Temporary credential location (owner-only, never printed): {credential_path}")
        print("First sign-in requires a password change. Approval UI integration remains separate work.")
        return 0
    except (ProvisionError, OSError, ValueError, KeyError) as error:
        message = str(error) if isinstance(error, ProvisionError) else "Invalid provisioning state; details withheld"
        print(f"FAIL {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())