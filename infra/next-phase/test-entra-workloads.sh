#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
registrations="$(mktemp)"
audiences="$(mktemp)"
pycache_dir="$(mktemp -d)"
cleanup() {
  rm -f "$registrations" "$audiences"
  rm -rf "$pycache_dir"
}
trap cleanup EXIT

cd "$project_dir"
python3 "$phase_dir/check_toolchain.py"
bash -n "$phase_dir/deploy-entra-workloads.sh"
bash -n "$phase_dir/test-entra-workloads.sh"
PYTHONPYCACHEPREFIX="$pycache_dir" python3 -m py_compile \
  "$phase_dir/entra_workload_parameters.py" \
  "$phase_dir/record_entra_workloads.py" \
  "$phase_dir/validate-entra-workloads.py"
az bicep build --file "$phase_dir/entra-applications.bicep" --stdout > "$registrations"
az bicep build --file "$phase_dir/entra-audiences.bicep" --stdout > "$audiences"
python3 "$phase_dir/validate-entra-workloads.py" "$registrations" "$audiences"
echo "PASS local Entra audience tests complete; no Entra or Azure resource was created."