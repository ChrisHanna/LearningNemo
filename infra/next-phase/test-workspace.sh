#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
compiled="$(mktemp)"
lock_compiled="$(mktemp)"
pycache="$(mktemp -d)"
test_python="${LEARNINGNEMO_TEST_PYTHON:-${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/nemo-agents}/bin/python}"
cleanup() {
  rm -f "$compiled" "$lock_compiled"
  rm -rf "$pycache"
}
trap cleanup EXIT

cd "$project_dir"
python3 "$phase_dir/check_toolchain.py"
bash -n \
  "$phase_dir/deploy-workspace.sh" \
  "$phase_dir/remove-workspace.sh" \
  "$phase_dir/test-workspace.sh" \
  "$project_dir/scripts/bootstrap-saw-openshell.sh"
PYTHONPYCACHEPREFIX="$pycache" python3 -m py_compile \
  "$phase_dir/preflight_workspace.py" \
  "$phase_dir/capture_workspace_diagnostics.py" \
  "$phase_dir/summarize_workspace_failure.py" \
  "$phase_dir/validate-workspace.py" \
  "$phase_dir/validate-workspace-runtime-lock.py" \
  "$phase_dir/validate-workspace-bootstrap-egress.py" \
  "$phase_dir/validate-workspace-openshell-bootstrap.py" \
  "$phase_dir/validate-workspace-subnet-egress.py" \
  "$phase_dir/validate_workspace_egress_what_if.py" \
  "$phase_dir/validate_workspace_bootstrap_what_if.py" \
  "$phase_dir/validate_workspace_what_if.py" \
  "$phase_dir/validate_workspace_lock_what_if.py" \
  "$phase_dir/verify_workspace.py" \
  "$phase_dir/workspace_parameters.py"
python3 "$phase_dir/workspace_parameters.py" validate \
  "$phase_dir/environments/dev.workspace.config.json"
az bicep build --file "$phase_dir/workspace.bicep" --stdout > "$compiled"
python3 "$phase_dir/validate-workspace.py" "$compiled"
az bicep build --file "$phase_dir/workspace-openshell-bootstrap.bicep" --stdout > "$compiled"
python3 "$phase_dir/validate-workspace-openshell-bootstrap.py" "$compiled"
az bicep build --file "$phase_dir/workspace-runtime-lock.bicep" --stdout > "$lock_compiled"
python3 "$phase_dir/validate-workspace-runtime-lock.py" "$lock_compiled"
az bicep build --file "$phase_dir/workspace-bootstrap-egress.bicep" --stdout > "$compiled"
python3 "$phase_dir/validate-workspace-bootstrap-egress.py" "$compiled"
az bicep build --file "$phase_dir/workspace-subnet-egress.bicep" --stdout > "$compiled"
python3 "$phase_dir/validate-workspace-subnet-egress.py" "$compiled"
"$test_python" -m pytest -q tests/test_workspace_infra.py
echo "PASS local SAW/OpenShell workspace gates complete; Azure was not queried or changed."