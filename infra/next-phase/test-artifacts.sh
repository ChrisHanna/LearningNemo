#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
config="$phase_dir/environments/dev.artifacts.config.json"
compiled="$(mktemp)"
parameters="$(mktemp)"
pycache="$(mktemp -d)"
test_python="${LEARNINGNEMO_TEST_PYTHON:-${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/nemo-agents}/bin/python}"
cleanup() {
  rm -f "$compiled" "$parameters"
  rm -rf "$pycache"
}
trap cleanup EXIT

cd "$project_dir"
bash -n "$phase_dir/deploy-artifacts.sh" "$phase_dir/remove-artifacts.sh" "$phase_dir/test-artifacts.sh"
PYTHONPYCACHEPREFIX="$pycache" python3 -m py_compile \
  "$phase_dir/artifact_contract.py" \
  "$phase_dir/artifact_parameters.py" \
  "$phase_dir/check_artifact_cleanup.py" \
  "$phase_dir/preflight_artifacts.py" \
  "$phase_dir/record_artifacts.py" \
  "$phase_dir/validate-artifacts.py" \
  "$phase_dir/verify_artifacts.py"
python3 "$phase_dir/artifact_parameters.py" materialize \
  "$config" \
  "$parameters" \
  --expires-at "$(date -u -d '+1 hour' +%Y-%m-%dT%H:%M:%SZ)"
python3 "$phase_dir/preflight_artifacts.py" \
  --offline \
  --config "$config" \
  --parameters "$parameters" \
  --platform-parameters "$phase_dir/environments/dev.platform.parameters.json" \
  --database-parameters "$phase_dir/environments/dev.database.parameters.json"
az bicep build --file "$phase_dir/artifact-registry-stack.bicep" --stdout > "$compiled"
python3 "$phase_dir/validate-artifacts.py" "$compiled"
"$test_python" -m pytest -q tests/test_artifact_infra.py tests/test_trusted_image.py
echo "PASS local artifact registry and trusted image gates complete; Azure was not queried or changed."