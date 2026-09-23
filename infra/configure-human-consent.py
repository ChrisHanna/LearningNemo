#!/usr/bin/env python3
"""Register review scope and consent it only for the owned Approver account."""

import argparse
from copy import deepcopy
from datetime import UTC, datetime
import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID, uuid5


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("approver_identity", ROOT / "infra/create-entra-approver.py")
IDENTITY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IDENTITY)
SCOPE = "plans.review"
REVIEW_SCOPES = frozenset({"agent.invoke", SCOPE})


def review_consent_scope(existing):
    return " ".join(sorted(set(existing.split()) | REVIEW_SCOPES))


def preauthorize_review(existing, client_id, scope_id):
    UUID(client_id)
    UUID(scope_id)
    result = deepcopy(existing)
    matches = [entry for entry in result if entry.get('appId') == client_id]
    if len(matches) != 1 or set(matches[0]) != {'appId', 'delegatedPermissionIds'}:
        raise ValueError('one existing owned client preauthorization required')
    identifiers = matches[0]['delegatedPermissionIds']
    if not isinstance(identifiers, list) or len(identifiers) != len(set(identifiers)):
        raise ValueError('ambiguous preauthorized permission inventory')
    if scope_id not in identifiers:
        identifiers.append(scope_id)
    return result


def merged_scopes(existing, api_id):
    identifier = str(uuid5(UUID(api_id), "delegated-scope:plans.review"))
    matches = [item for item in existing if item["value"] == SCOPE]
    if matches:
        if len(matches) != 1 or not matches[0].get("isEnabled") or matches[0].get("type") != "Admin":
            raise ValueError("existing review scope differs")
        return existing, matches[0]["id"]
    if any(item["id"] == identifier for item in existing):
        raise ValueError("scope identity collision")
    return [*existing, {"id": identifier, "value": SCOPE, "type": "Admin", "isEnabled": True,
        "adminConsentDisplayName": "Review exact LearningNeMo plans",
        "adminConsentDescription": "Allows independently assigned Approvers to review exact submitted remediation plans. Does not permit execution.",
        "userConsentDisplayName": None, "userConsentDescription": None}], identifier


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--preauthorize-review", action="store_true")
    args = parser.parse_args()
    settings = json.loads((ROOT / ".nemo-test-client.json").read_text())
    account = IDENTITY.az(["account", "show"])
    if account["id"] != os.environ["AZURE_SUBSCRIPTION_ID"] or account["tenantId"] != settings["ENTRA_TENANT_ID"]:
        raise ValueError("explicit tenant/subscription match required")
    record = IDENTITY.read_private(Path.home() / ".local/state/learningnemo/approver-dev.identity.json")
    if record["tenantId"] != account["tenantId"] or record["apiClientId"] != settings["ENTRA_CLIENT_ID"]:
        raise ValueError("Approver ownership record differs")
    api = IDENTITY.az(["ad", "app", "show", "--id", settings["ENTRA_CLIENT_ID"]])
    client = IDENTITY.az(["ad", "app", "show", "--id", settings["ENTRA_PUBLIC_CLIENT_ID"]])
    api_sp = IDENTITY.az(["ad", "sp", "show", "--id", settings["ENTRA_CLIENT_ID"]])
    client_sp = IDENTITY.az(["ad", "sp", "show", "--id", settings["ENTRA_PUBLIC_CLIENT_ID"]])
    assignments = IDENTITY.graph("GET", f"users/{record['userId']}/appRoleAssignments")
    if assignments.get("@odata.nextLink"):
        raise ValueError("unexpected paginated assignments")
    approver_roles = [role["id"] for role in api["appRoles"] if role["value"] == "Task.Approver"]
    if len(approver_roles) != 1 or {row["appRoleId"] for row in assignments["value"] if row["resourceId"] == api_sp["id"]} != set(approver_roles):
        raise ValueError("dedicated Approver-only assignment required")
    scopes, scope_id = merged_scopes(api["api"].get("oauth2PermissionScopes", []), api["appId"])
    if args.preauthorize_review:
        if scopes != api['api'].get('oauth2PermissionScopes', []):
            raise ValueError('review scope must already be registered')
        expected = preauthorize_review(api['api'].get('preAuthorizedApplications', []), client['appId'], scope_id)
        print('PLAN preauthorize only plans.review for the existing client; no role, directory, or execution grant', flush=True)
        if not args.apply:
            return
        if os.environ.get('LEARNINGNEMO_AZURE_APPLY') != 'review-client-preauthorization':
            raise ValueError('explicit review-client-preauthorization acknowledgement required')
        state = Path.home() / '.local/state/learningnemo'
        stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')
        IDENTITY.write_private(state / f'review-preauthorization.before-{stamp}.json',
            {'api': api, 'client': client, 'assignments': assignments}, exclusive=True)
        IDENTITY.graph('PATCH', f"applications/{api['id']}", {'api': {'preAuthorizedApplications': expected}})
        after = IDENTITY.az(['ad', 'app', 'show', '--id', api['appId']])
        for name in ('appRoles', 'signInAudience', 'identifierUris', 'requiredResourceAccess'):
            if after.get(name) != api.get(name):
                raise ValueError('unrelated API authorization configuration changed')
        if after['api'].get('preAuthorizedApplications') != expected:
            raise ValueError('review preauthorization not confirmed')
        for name, value in api['api'].items():
            if name != 'preAuthorizedApplications' and after['api'].get(name) != value:
                raise ValueError('another API setting changed')
        current_client = IDENTITY.az(['ad', 'app', 'show', '--id', client['appId']])
        if current_client.get('requiredResourceAccess') != client.get('requiredResourceAccess'):
            raise ValueError('client requested permissions changed')
        current_assignments = IDENTITY.graph('GET', f"users/{record['userId']}/appRoleAssignments")
        if sorted(current_assignments['value'], key=lambda row: row['id']) != sorted(assignments['value'], key=lambda row: row['id']):
            raise ValueError('Approver role assignments changed')
        IDENTITY.write_private(state / 'review-preauthorization.verified.json', {
            'tenantId': account['tenantId'], 'apiClientId': api['appId'], 'publicClientId': client['appId'],
            'scope': SCOPE, 'scopeId': scope_id, 'verifiedAt': datetime.now(UTC).isoformat(),
            'roleAssignmentsUnchanged': True, 'consentBoundary': 'client-specific-not-per-user'})
        print('PASS client-specific plans.review preauthorization verified; API roles, other clients, and requested permissions preserved', flush=True)
        return
    permissions = list(client.get("requiredResourceAccess", []))
    resource = next((item for item in permissions if item["resourceAppId"] == api["appId"]), None)
    if resource is None:
        raise ValueError("expected existing LearningNeMo client/API relationship")
    if not any(item["id"] == scope_id and item["type"] == "Scope" for item in resource["resourceAccess"]):
        resource["resourceAccess"].append({"id": scope_id, "type": "Scope"})
    query = urlencode({"$filter": f"clientId eq '{client_sp['id']}' and resourceId eq '{api_sp['id']}'"})
    grants = IDENTITY.graph("GET", "oauth2PermissionGrants?" + query)
    if grants.get("@odata.nextLink"):
        raise ValueError("unexpected paginated grants")
    if any(row["consentType"] == "AllPrincipals" and SCOPE in row.get("scope", "").split() for row in grants["value"]):
        raise ValueError("review consent already tenant-wide; review scope before continuing")
    personal = [row for row in grants["value"] if row["consentType"] == "Principal" and row["principalId"] == record["userId"]]
    if len(personal) > 1:
        raise ValueError("ambiguous personal consent")
    before = {"api": api, "client": client, "grants": grants}
    IDENTITY.write_private(Path.home() / ".local/state/learningnemo/human-consent.before.json", before)
    print("PLAN consent agent.invoke and plans.review only for the owned Approver; preserve other scopes and role assignments")
    print("Approver account: " + record["userPrincipalName"])
    if not args.apply:
        return
    if os.environ.get("LEARNINGNEMO_AZURE_APPLY") != "human-consent":
        raise ValueError("human-consent acknowledgement required")
    IDENTITY.graph("PATCH", f"applications/{api['id']}", {"api": {"oauth2PermissionScopes": scopes}})
    IDENTITY.graph("PATCH", f"applications/{client['id']}", {"requiredResourceAccess": permissions})
    scope = review_consent_scope(personal[0].get("scope", "") if personal else "")
    if personal:
        IDENTITY.graph("PATCH", f"oauth2PermissionGrants/{personal[0]['id']}", {"scope": scope})
    else:
        IDENTITY.graph("POST", "oauth2PermissionGrants", {"clientId": client_sp["id"], "resourceId": api_sp["id"],
            "consentType": "Principal", "principalId": record["userId"], "scope": scope})
    after = IDENTITY.graph("GET", "oauth2PermissionGrants?" + query)
    if not any(row["consentType"] == "Principal" and row["principalId"] == record["userId"] and REVIEW_SCOPES <= set(row["scope"].split()) for row in after["value"]):
        raise ValueError("per-user review consent not verified")
    other_before = {row['id']: row for row in grants['value'] if row.get('principalId') != record['userId']}
    other_after = {row['id']: row for row in after['value'] if row.get('principalId') != record['userId']}
    if other_before != other_after:
        raise ValueError('another consent grant changed; inspect before proceeding')
    IDENTITY.write_private(Path.home() / ".local/state/learningnemo/human-consent.verified.json", {"scope": scope, "principalId": record["userId"], "tenantId": account["tenantId"]})
    print("PASS review scope registered and consented for dedicated Approver only; role assignments unchanged")


if __name__ == "__main__":
    main()