#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
config_file="$phase_dir/environments/dev.workloads.config.json"
compiled_template="$(mktemp)"
pycache_dir="$(mktemp -d)"
test_python="${LEARNINGNEMO_TEST_PYTHON:-${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/nemo-agents}/bin/python}"
cleanup() {
  rm -f "$compiled_template"
  rm -rf "$pycache_dir"
}
trap cleanup EXIT

cd "$project_dir"
if [[ ! -x "$test_python" ]] || ! "$test_python" -c 'import pytest' 2>/dev/null; then
  echo "Set LEARNINGNEMO_TEST_PYTHON to the locked project Python environment with pytest installed." >&2
  exit 1
fi
python3 "$phase_dir/check_toolchain.py"
bash -n "$phase_dir/test-workloads.sh"
bash -n "$phase_dir/deploy-workloads.sh"
bash -n "$phase_dir/remove-workloads.sh"
PYTHONPYCACHEPREFIX="$pycache_dir" python3 -m py_compile \
  "$phase_dir/check_workload_cleanup.py" \
  "$phase_dir/preflight_workloads.py" \
  "$phase_dir/record_workloads.py" \
  "$phase_dir/validate-workloads.py" \
  "$phase_dir/verify_workloads.py" \
  "$phase_dir/workload_contract.py" \
  "$phase_dir/workload_parameters.py" \
  "$phase_dir/workload_release.py" \
  "$phase_dir/trusted_image_release.py" \
  "$phase_dir/verify_image_elf.py"
python3 "$phase_dir/workload_parameters.py" validate "$config_file"
az bicep build --file "$phase_dir/workloads.bicep" --stdout > "$compiled_template"
python3 "$phase_dir/validate-workloads.py" "$compiled_template"
"$test_python" -m pytest -q \
  tests/test_next_phase_workload_infra.py \
  tests/test_trusted_image.py \
  tests/test_trusted_image_release.py \
  tests/test_trusted_runtime.py \
  tests/test_trusted_workflow.py \
  tests/test_mssql_client.py \
  tests/test_mssql_owned_query.py \
  tests/test_sql_adapters.py
echo "PASS local trusted workload tests complete; no image or Azure resource was created."