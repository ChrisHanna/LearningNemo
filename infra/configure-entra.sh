#!/usr/bin/env bash
set -euo pipefail

apply=false
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      apply=true
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

: "${ENTRA_TENANT_ID:?Set ENTRA_TENANT_ID}"
: "${ENTRA_CLIENT_ID:?Set ENTRA_CLIENT_ID to the agent API app ID}"
: "${ENTRA_PUBLIC_CLIENT_ID:?Set ENTRA_PUBLIC_CLIENT_ID to the LearningNeMo client app ID}"
legacy_reader_client_id="${ENTRA_LEGACY_READER_CLIENT_ID:-}"

active_tenant="$(az account show --query tenantId --output tsv)"
if [[ "$active_tenant" != "$ENTRA_TENANT_ID" ]]; then
  echo "Azure CLI is signed in to a different tenant." >&2
  exit 1
fi

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

az ad app show --id "$ENTRA_CLIENT_ID" --output json > "$work_dir/api-app.json"
az ad app show --id "$ENTRA_PUBLIC_CLIENT_ID" --output json > "$work_dir/client-app.json"
if [[ -n "$legacy_reader_client_id" ]]; then
    az ad app show --id "$legacy_reader_client_id" --output json > "$work_dir/legacy-client-app.json"
fi

python3 - "$work_dir" "$ENTRA_CLIENT_ID" "$ENTRA_PUBLIC_CLIENT_ID" "$legacy_reader_client_id" <<'PY'
import json
import sys
import uuid
from copy import deepcopy
from pathlib import Path

work_dir = Path(sys.argv[1])
api_app_id = sys.argv[2]
client_app_id = sys.argv[3]
legacy_client_app_id = sys.argv[4] or None

api_app = json.loads((work_dir / "api-app.json").read_text())
client_app = json.loads((work_dir / "client-app.json").read_text())
legacy_client_app = (
    json.loads((work_dir / "legacy-client-app.json").read_text())
    if legacy_client_app_id
    else None
)

scope_specs = (
    (
        "agent.invoke",
        "Access the task agent",
        "Allow the application to invoke the task agent on your behalf.",
        "User",
    ),
    (
        "tasks.read",
        "Read tasks",
        "Allow the application to read tasks on your behalf.",
        "User",
    ),
    (
        "tasks.execute",
        "Execute tasks",
        "Allow the application to execute tasks on your behalf.",
        "Admin",
    ),
)

app_role_specs = (
    (
        "Task.Reader",
        "Task Reader",
        "Can invoke LearningNeMo and read task state.",
    ),
    (
        "Task.Operator",
        "Task Operator",
        "Can execute and reset LearningNeMo tasks.",
    ),
)

api_settings = dict(api_app.get("api") or {})
existing_scopes = list(api_settings.get("oauth2PermissionScopes") or [])
scopes_by_value = {scope.get("value"): scope for scope in existing_scopes}

configured_scope_ids = {}
for value, display_name, description, consent_type in scope_specs:
    scope = scopes_by_value.get(value)
    if scope is None:
        scope = {
            "id": str(uuid.uuid5(uuid.UUID(api_app_id), value)),
            "value": value,
        }
        existing_scopes.append(scope)
    scope.update(
        {
            "adminConsentDisplayName": display_name,
            "adminConsentDescription": description,
            "userConsentDisplayName": display_name,
            "userConsentDescription": description,
            "type": consent_type,
            "isEnabled": True,
        }
    )
    configured_scope_ids[value] = scope["id"]

existing_app_roles = list(api_app.get("appRoles") or [])
app_roles_by_value = {role.get("value"): role for role in existing_app_roles}
configured_role_ids = {}
for value, display_name, description in app_role_specs:
    role = app_roles_by_value.get(value)
    if role is None:
        role = {
            "id": str(uuid.uuid5(uuid.UUID(api_app_id), f"app-role:{value}")),
            "value": value,
        }
        existing_app_roles.append(role)
    role.update(
        {
            "allowedMemberTypes": ["User"],
            "description": description,
            "displayName": display_name,
            "isEnabled": True,
        }
    )
    configured_role_ids[value] = role["id"]

api_settings["oauth2PermissionScopes"] = existing_scopes
api_settings["requestedAccessTokenVersion"] = 2

scope_api_settings = deepcopy(api_settings)
preauthorized = deepcopy(api_settings.get("preAuthorizedApplications") or [])
managed_scope_ids = set(configured_scope_ids.values())


def configure_preauthorization(target_client_app_id, scope_values):
    client = next(
        (item for item in preauthorized if item.get("appId") == target_client_app_id),
        None,
    )
    if client is None:
        client = {"appId": target_client_app_id, "delegatedPermissionIds": []}
        preauthorized.append(client)
    existing_ids = set(client.get("delegatedPermissionIds") or [])
    unmanaged_ids = existing_ids - managed_scope_ids
    desired_ids = {configured_scope_ids[value] for value in scope_values}
    client["delegatedPermissionIds"] = sorted(unmanaged_ids | desired_ids)


configure_preauthorization(client_app_id, ("agent.invoke", "tasks.read", "tasks.execute"))
if legacy_client_app_id:
    legacy_entry = next(
        (item for item in preauthorized if item.get("appId") == legacy_client_app_id),
        None,
    )
    if legacy_entry is not None:
        legacy_entry["delegatedPermissionIds"] = sorted(
            set(legacy_entry.get("delegatedPermissionIds") or []) - managed_scope_ids
        )
preauthorized = [
    entry
    for entry in preauthorized
    if entry.get("delegatedPermissionIds")
]
api_settings["preAuthorizedApplications"] = preauthorized

identifier_uris = list(api_app.get("identifierUris") or [])
api_identifier = f"api://{api_app_id}"
if api_identifier not in identifier_uris:
    identifier_uris.append(api_identifier)

api_scopes_patch = {
    "displayName": "LearningNeMo API",
    "identifierUris": identifier_uris,
    "api": scope_api_settings,
    "appRoles": existing_app_roles,
}
api_preauthorization_patch = {"api": api_settings}


def build_client_patch(client_app, scope_values):
    required_access = list(client_app.get("requiredResourceAccess") or [])
    api_access = next(
        (item for item in required_access if item.get("resourceAppId") == api_app_id),
        None,
    )
    if api_access is None:
        api_access = {"resourceAppId": api_app_id, "resourceAccess": []}
        required_access.append(api_access)

    resource_access = [
        item
        for item in api_access.get("resourceAccess") or []
        if item.get("id") not in managed_scope_ids
    ]
    resource_access.extend(
        {"id": configured_scope_ids[value], "type": "Scope"}
        for value in scope_values
    )
    api_access["resourceAccess"] = resource_access

    redirect_uris = list((client_app.get("publicClient") or {}).get("redirectUris") or [])
    if "http://localhost" not in redirect_uris:
        redirect_uris.append("http://localhost")

    return {
        "displayName": "LearningNeMo Local Client",
        "isFallbackPublicClient": True,
        "publicClient": {"redirectUris": redirect_uris},
        "requiredResourceAccess": required_access,
    }


client_patch = build_client_patch(
    client_app,
    ("agent.invoke", "tasks.read", "tasks.execute"),
)

legacy_client_patch = None
if legacy_client_app is not None:
    legacy_required_access = list(legacy_client_app.get("requiredResourceAccess") or [])
    legacy_api_access = next(
        (item for item in legacy_required_access if item.get("resourceAppId") == api_app_id),
        None,
    )
    if legacy_api_access is not None:
        legacy_api_access["resourceAccess"] = [
            item
            for item in legacy_api_access.get("resourceAccess") or []
            if item.get("id") not in managed_scope_ids
        ]
        if not legacy_api_access["resourceAccess"]:
            legacy_required_access.remove(legacy_api_access)
    legacy_client_patch = {"requiredResourceAccess": legacy_required_access}

(work_dir / "api-scopes-patch.json").write_text(json.dumps(api_scopes_patch, separators=(",", ":")))
(work_dir / "api-preauthorization-patch.json").write_text(
    json.dumps(api_preauthorization_patch, separators=(",", ":"))
)
(work_dir / "client-patch.json").write_text(json.dumps(client_patch, separators=(",", ":")))
if legacy_client_patch is not None:
    (work_dir / "legacy-client-patch.json").write_text(
        json.dumps(legacy_client_patch, separators=(",", ":"))
    )

print("Planned delegated scopes:")
for value, _, _, consent_type in scope_specs:
    print(f"  {value} ({consent_type} consent)")
print("LearningNeMo client scopes: agent.invoke, tasks.read, tasks.execute")
print("API app roles: Task.Reader, Task.Operator")
print("Application names: LearningNeMo API, LearningNeMo Local Client")
print("Public client redirect URI: http://localhost")
PY

if [[ "$apply" != true ]]; then
  echo "Dry run only. Re-run with --apply to update Microsoft Entra ID."
  exit 0
fi

api_object_id="$(az ad app show --id "$ENTRA_CLIENT_ID" --query id --output tsv)"
client_object_id="$(az ad app show --id "$ENTRA_PUBLIC_CLIENT_ID" --query id --output tsv)"

az rest \
  --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/$api_object_id" \
  --headers Content-Type=application/json \
    --body "@$work_dir/api-scopes-patch.json" \
    --output none

az rest \
    --method PATCH \
    --uri "https://graph.microsoft.com/v1.0/applications/$api_object_id" \
    --headers Content-Type=application/json \
    --body "@$work_dir/api-preauthorization-patch.json" \
  --output none

az rest \
  --method PATCH \
    --uri "https://graph.microsoft.com/v1.0/applications/$client_object_id" \
  --headers Content-Type=application/json \
    --body "@$work_dir/client-patch.json" \
    --output none

if [[ -n "$legacy_reader_client_id" ]]; then
    legacy_client_object_id="$(az ad app show --id "$legacy_reader_client_id" --query id --output tsv)"
    az rest \
        --method PATCH \
        --uri "https://graph.microsoft.com/v1.0/applications/$legacy_client_object_id" \
        --headers Content-Type=application/json \
        --body "@$work_dir/legacy-client-patch.json" \
        --output none
fi

az ad sp show --id "$ENTRA_CLIENT_ID" --output none 2>/dev/null || \
  az ad sp create --id "$ENTRA_CLIENT_ID" --output none
az ad sp show --id "$ENTRA_PUBLIC_CLIENT_ID" --output none 2>/dev/null || \
    az ad sp create --id "$ENTRA_PUBLIC_CLIENT_ID" --output none

python3 - \
    "$repo_root/.nemo-test-client.json" \
    "$ENTRA_TENANT_ID" \
    "$ENTRA_CLIENT_ID" \
    "$ENTRA_PUBLIC_CLIENT_ID" <<'PY'
import json
import sys
from pathlib import Path

settings_path = Path(sys.argv[1])
settings = {
        "ENTRA_TENANT_ID": sys.argv[2],
        "ENTRA_CLIENT_ID": sys.argv[3],
        "ENTRA_PUBLIC_CLIENT_ID": sys.argv[4],
}
settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
PY

echo "Microsoft Entra configuration applied."
echo "Local non-secret test settings written to .nemo-test-client.json."