#!/usr/bin/env bash
set -euo pipefail

apply=false

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
: "${ENTRA_READER_USER:?Set ENTRA_READER_USER to the Reader test user's UPN or object ID}"
: "${ENTRA_OPERATOR_USER:?Set ENTRA_OPERATOR_USER to the Operator test user's UPN or object ID}"

active_tenant="$(az account show --query tenantId --output tsv)"
if [[ "$active_tenant" != "$ENTRA_TENANT_ID" ]]; then
  echo "Azure CLI is signed in to a different tenant." >&2
  exit 1
fi

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

az ad app show --id "$ENTRA_CLIENT_ID" --output json > "$work_dir/api-app.json"
az ad sp show --id "$ENTRA_CLIENT_ID" --output json > "$work_dir/api-sp.json"
az ad user show --id "$ENTRA_READER_USER" --output json > "$work_dir/reader-user.json"
az ad user show --id "$ENTRA_OPERATOR_USER" --output json > "$work_dir/operator-user.json"

api_sp_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$work_dir/api-sp.json")"
echo '[]' > "$work_dir/assignments.json"
next_uri="https://graph.microsoft.com/v1.0/servicePrincipals/$api_sp_id/appRoleAssignedTo?%24top=999"
page_number=0
while [[ -n "$next_uri" ]]; do
  page_number=$((page_number + 1))
  az rest \
    --method GET \
    --uri "$next_uri" \
    --output json > "$work_dir/assignments-page.json"
  python3 - "$work_dir" <<'PY'
import json
import sys
from pathlib import Path

work_dir = Path(sys.argv[1])
assignments_path = work_dir / "assignments.json"
assignments = json.loads(assignments_path.read_text())
page = json.loads((work_dir / "assignments-page.json").read_text())
assignments.extend(page.get("value", []))
assignments_path.write_text(json.dumps(assignments, separators=(",", ":")))
(work_dir / "next-uri.txt").write_text(page.get("@odata.nextLink", ""))
PY
  next_uri="$(cat "$work_dir/next-uri.txt")"
done

python3 - "$work_dir" <<'PY'
import json
import sys
from pathlib import Path

work_dir = Path(sys.argv[1])
api_app = json.loads((work_dir / "api-app.json").read_text())
api_sp = json.loads((work_dir / "api-sp.json").read_text())
reader = json.loads((work_dir / "reader-user.json").read_text())
operator = json.loads((work_dir / "operator-user.json").read_text())
assignments = json.loads((work_dir / "assignments.json").read_text())

if reader["id"] == operator["id"]:
    raise SystemExit("Reader and Operator must be different Entra users.")

roles = {role.get("value"): role["id"] for role in api_app.get("appRoles", [])}
required_roles = {"Task.Reader", "Task.Operator"}
missing = required_roles - roles.keys()
if missing:
    raise SystemExit(f"Configure API app roles first: {', '.join(sorted(missing))}")

desired = {
    reader["id"]: {roles["Task.Reader"]},
    operator["id"]: {roles["Task.Reader"], roles["Task.Operator"]},
}
managed_role_ids = {roles[value] for value in required_roles}
existing = set()
deletes = []
for assignment in assignments:
    pair = (assignment.get("principalId"), assignment.get("appRoleId"))
    if pair[1] not in managed_role_ids or assignment.get("principalType") != "User":
        continue
    if pair[0] in desired and pair[1] in desired[pair[0]]:
        existing.add(pair)
    else:
        deletes.append(assignment["id"])

adds = []
for principal_id, role_ids in desired.items():
    for role_id in role_ids:
        if (principal_id, role_id) not in existing:
            adds.append(
                {
                    "principalId": principal_id,
                    "resourceId": api_sp["id"],
                    "appRoleId": role_id,
                }
            )

(work_dir / "delete-assignments.txt").write_text("\n".join(deletes) + ("\n" if deletes else ""))
(work_dir / "add-assignments.jsonl").write_text(
    "\n".join(json.dumps(item, separators=(",", ":")) for item in adds) + ("\n" if adds else "")
)
print("Planned assignments:")
print("  Reader user: Task.Reader")
print("  Operator user: Task.Reader, Task.Operator")
print("  Preserve managed group/service-principal assignments")
print(f"  Remove obsolete assignments: {len(deletes)}")
print(f"  Add missing assignments: {len(adds)}")
PY

if [[ "$apply" != true ]]; then
  echo "Dry run only. Re-run with --apply to reconcile test-user assignments."
  exit 0
fi

while IFS= read -r assignment_id; do
  [[ -z "$assignment_id" ]] && continue
  az rest \
    --method DELETE \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$api_sp_id/appRoleAssignedTo/$assignment_id" \
    --output none
done < "$work_dir/delete-assignments.txt"

while IFS= read -r assignment; do
  [[ -z "$assignment" ]] && continue
  az rest \
    --method POST \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$api_sp_id/appRoleAssignedTo" \
    --headers Content-Type=application/json \
    --body "$assignment" \
    --output none
done < "$work_dir/add-assignments.jsonl"

az rest \
  --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$api_sp_id" \
  --headers Content-Type=application/json \
  --body '{"appRoleAssignmentRequired":true}' \
  --output none

echo "Test-user app-role assignments applied."