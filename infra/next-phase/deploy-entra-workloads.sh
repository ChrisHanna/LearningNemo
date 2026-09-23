#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

mode="what-if"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
config_file="${WORKLOAD_CONFIG_FILE:-$phase_dir/environments/dev.workloads.config.json}"
registrations_template="$phase_dir/entra-applications.bicep"
audiences_template="$phase_dir/entra-audiences.bicep"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"

python3 "$phase_dir/check_toolchain.py"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      mode="apply"
      shift
      ;;
    --what-if)
      mode="what-if"
      shift
      ;;
    --config)
      config_file="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

python3 "$phase_dir/workload_parameters.py" validate "$config_file"
get_config() {
  python3 "$phase_dir/workload_parameters.py" get-config "$config_file" "$1"
}
environment="$(get_config environment)"
project_name="$(get_config projectName)"
resource_group="$(get_config platformResourceGroupName)"

compiled_registrations="$(mktemp)"
compiled_audiences="$(mktemp)"
private_auth="$(mktemp)"
private_parameters="$(mktemp)"
what_if_result="$(mktemp)"
cleanup() {
  rm -f "$compiled_registrations" "$compiled_audiences" "$private_auth" "$private_parameters" "$what_if_result"
}
trap cleanup EXIT
chmod 600 "$private_auth" "$private_parameters"
az bicep build --file "$registrations_template" --stdout > "$compiled_registrations"
az bicep build --file "$audiences_template" --stdout > "$compiled_audiences"
python3 "$phase_dir/validate-entra-workloads.py" "$compiled_registrations" "$compiled_audiences"

az account show --output none
registration_state="$(python3 "$phase_dir/entra_workload_parameters.py" status --config "$config_file")"
registration_name="$project_name-workload-applications-$environment"
audience_name="$project_name-workload-audiences-$environment"

if [[ "$registration_state" == "absent" ]]; then
  az deployment group validate \
    --name "$registration_name" \
    --resource-group "$resource_group" \
    --template-file "$registrations_template" \
    --parameters environment="$environment" projectName="$project_name" \
    --output none
  if [[ "$mode" == "what-if" ]]; then
    echo "Running registration-stage Graph what-if. No resources will be changed."
    az deployment group what-if \
      --name "$registration_name" \
      --resource-group "$resource_group" \
      --template-file "$registrations_template" \
      --parameters environment="$environment" projectName="$project_name" \
      --result-format ResourceIdOnly \
      --no-pretty-print \
      --output json > "$what_if_result"
    python3 "$phase_dir/summarize_what_if.py" "$what_if_result"
    echo "Audience-stage what-if is deferred until Entra has generated the four application IDs."
    exit 0
  fi
elif [[ "$registration_state" != "complete" ]]; then
  echo "Apply blocked: workload audience registration state is unknown." >&2
  exit 1
fi

if [[ "$mode" == "apply" ]]; then
  if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
    echo "Apply blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription." >&2
    exit 2
  fi
  active_subscription="$(az account show --query id --output tsv)"
  if [[ "$active_subscription" != "$AZURE_SUBSCRIPTION_ID" ]]; then
    echo "Apply blocked: active subscription does not match AZURE_SUBSCRIPTION_ID." >&2
    exit 1
  fi
  unset active_subscription
  if [[ "${LEARNINGNEMO_AZURE_APPLY:-}" != "trusted-workload-audiences" ]]; then
    echo "Apply blocked. Set LEARNINGNEMO_AZURE_APPLY=trusted-workload-audiences for this command only." >&2
    exit 2
  fi
  if [[ "$registration_state" == "absent" ]]; then
    echo "Creating four persistent, credentialless workload audience registrations through Graph Bicep."
    az deployment group create \
      --name "$registration_name" \
      --resource-group "$resource_group" \
      --template-file "$registrations_template" \
      --parameters environment="$environment" projectName="$project_name" \
      --output none
  fi
fi

python3 "$phase_dir/entra_workload_parameters.py" materialize \
  --config "$config_file" \
  --auth-output "$private_auth" \
  --parameters-output "$private_parameters"
az deployment group validate \
  --name "$audience_name" \
  --resource-group "$resource_group" \
  --template-file "$audiences_template" \
  --parameters "@$private_parameters" \
  --output none
echo "Running audience-stage Graph what-if with private generated IDs."
az deployment group what-if \
  --name "$audience_name" \
  --resource-group "$resource_group" \
  --template-file "$audiences_template" \
  --parameters "@$private_parameters" \
  --result-format ResourceIdOnly \
  --no-pretty-print \
  --output json > "$what_if_result"
python3 "$phase_dir/summarize_what_if.py" "$what_if_result"
if [[ "$mode" == "what-if" ]]; then
  echo "What-if complete. Re-run with --apply and the explicit acknowledgement to converge audiences."
  exit 0
fi

echo "Converging four tenant-only v2 audiences and service principals through Graph Bicep."
az deployment group create \
  --name "$audience_name" \
  --resource-group "$resource_group" \
  --template-file "$audiences_template" \
  --parameters "@$private_parameters" \
  --output none
python3 "$phase_dir/entra_workload_parameters.py" verify \
  --config "$config_file" \
  --auth-file "$private_auth"

mkdir -p "$state_dir"
state_auth="$state_dir/workloads-$environment.auth.json"
state_manifest="$state_dir/workloads-$environment.entra-manifest.json"
install -m 600 "$private_auth" "$state_auth"
python3 "$phase_dir/record_entra_workloads.py" \
  --config "$config_file" \
  --auth-file "$private_auth" \
  --registrations-template "$compiled_registrations" \
  --audiences-template "$compiled_audiences" \
  --output "$state_manifest"
echo "Persistent WP2b Entra audiences are deployed and verified with no credentials or redirect URIs."
echo "Generated identifiers remain in owner-only state; the sanitized manifest is $state_manifest."