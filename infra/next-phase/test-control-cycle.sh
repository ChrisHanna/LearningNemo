#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
compiled="$(mktemp)"
pycache="$(mktemp -d)"
test_python="${LEARNINGNEMO_TEST_PYTHON:-${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/nemo-agents}/bin/python}"
cleanup() {
  rm -f "$compiled"
  rm -rf "$pycache"
}
trap cleanup EXIT

cd "$project_dir"
if [[ ! -x "$test_python" ]] || ! "$test_python" -c 'import pytest' 2>/dev/null; then
  echo "Set LEARNINGNEMO_TEST_PYTHON to the locked project Python environment with pytest installed." >&2
  exit 1
fi
python3 "$phase_dir/check_toolchain.py"
bash -n \
  "$phase_dir/deploy-control-cycle.sh" \
  "$phase_dir/remove-control-cycle.sh" \
  "$phase_dir/test-control-cycle.sh"
PYTHONPYCACHEPREFIX="$pycache" python3 -m py_compile \
  "$phase_dir/control_cycle_parameters.py" \
  "$phase_dir/preflight_control_cycle.py" \
  "$phase_dir/summarize_control_cycle.py" \
  "$phase_dir/validate-control-cycle.py" \
  "$phase_dir/validate_control_cycle_what_if.py" \
  "$phase_dir/verify_control_cycle.py" \
  "$phase_dir/verify_control_cycle_evidence.py" \
  "$project_dir/scripts/run-live-cycle-workflow.py"
python3 "$phase_dir/control_cycle_parameters.py" validate \
  "$phase_dir/environments/dev.control-cycle.config.json"
az bicep build --file "$phase_dir/control-cycle-stack.bicep" --stdout > "$compiled"
python3 "$phase_dir/validate-control-cycle.py" "$compiled"
"$test_python" -m pytest -q \
  tests/test_control_cycle_infra.py \
  tests/test_live_cycle_workflow.py \
  tests/test_mssql_owned_query.py \
  tests/test_sql_adapters.py
echo "PASS local control-cycle gates complete; Azure was not queried or changed."