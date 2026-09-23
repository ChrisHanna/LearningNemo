#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

apply=false
phase="foundation"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
provider_contract="$phase_dir/provider-phases.json"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      apply=true
      shift
      ;;
    --phase)
      phase="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

python3 "$phase_dir/check_toolchain.py"
az account show --output none
if [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  active_subscription="$(az account show --query id --output tsv)"
  if [[ "$active_subscription" != "$AZURE_SUBSCRIPTION_ID" ]]; then
    echo "Provider setup blocked: active subscription does not match AZURE_SUBSCRIPTION_ID." >&2
    exit 1
  fi
  unset active_subscription
fi

mapfile -t providers < <(python3 - "$provider_contract" "$phase" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
selected = sys.argv[2]
document = json.loads(path.read_text(encoding="utf-8"))
if document.get("schemaVersion") != 1 or not isinstance(document.get("phases"), dict):
    raise SystemExit("Invalid provider phase contract")
phases = document["phases"]
if selected == "all":
    names = sorted({name for values in phases.values() for name in values})
elif selected in phases:
    names = phases[selected]
else:
    raise SystemExit(f"Unknown provider phase: {selected}")
if not names or not all(isinstance(name, str) and name.startswith("Microsoft.") for name in names):
    raise SystemExit("Provider phase contains an invalid namespace")
print("\n".join(names))
PY
)

if [[ ${#providers[@]} -eq 0 ]]; then
  echo "No providers found for phase $phase." >&2
  exit 1
fi

missing=()
echo "Provider state for phase $phase:"
for provider in "${providers[@]}"; do
  state="$(az provider show --namespace "$provider" --query registrationState --output tsv 2>/dev/null || true)"
  if [[ "$state" == "Registered" ]]; then
    echo "  $provider: registered"
  else
    echo "  $provider: registration required"
    missing+=("$provider")
  fi
done

if [[ "$apply" != true ]]; then
  echo "Dry run only. No provider registrations were changed."
  exit 0
fi

if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Apply blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription before any mutation." >&2
  exit 2
fi
if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "provider-registration" ]]; then
  echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=provider-registration for this command only." >&2
  exit 2
fi

for provider in "${missing[@]}"; do
  echo "Registering $provider."
  az provider register --namespace "$provider" --wait --output none
done

for provider in "${providers[@]}"; do
  state="$(az provider show --namespace "$provider" --query registrationState --output tsv)"
  if [[ "$state" != "Registered" ]]; then
    echo "Provider registration did not complete: $provider" >&2
    exit 1
  fi
done

echo "Provider registration complete for phase $phase."