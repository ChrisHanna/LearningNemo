#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

azure=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --azure)
      azure=true
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
parameter_file="$phase_dir/environments/dev.parameters.json"
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
bash -n "$phase_dir/deploy-foundation.sh"
bash -n "$phase_dir/register-providers.sh"
bash -n "$phase_dir/remove-foundation.sh"
bash -n "$phase_dir/test-foundation.sh"
PYTHONPYCACHEPREFIX="$pycache_dir" python3 -m py_compile \
  "$phase_dir/check_toolchain.py" \
  "$phase_dir/check_foundation_cleanup.py" \
  "$phase_dir/foundation_parameters.py" \
  "$phase_dir/preflight.py" \
  "$phase_dir/record_foundation.py" \
  "$phase_dir/snapshot_foundation.py" \
  "$phase_dir/summarize_what_if.py" \
  "$phase_dir/validate-foundation.py" \
  "$phase_dir/verify_foundation.py"
python3 "$phase_dir/foundation_parameters.py" validate "$parameter_file"
python3 "$phase_dir/preflight.py" --offline --parameters "$parameter_file"
az bicep build --file "$phase_dir/foundation.bicep" --stdout > "$compiled_template"
python3 "$phase_dir/validate-foundation.py" "$compiled_template"
"$test_python" -m pytest -q tests/test_next_phase_infra.py

if [[ "$azure" == true ]]; then
  bash "$phase_dir/register-providers.sh" --phase foundation
  bash "$phase_dir/deploy-foundation.sh" --what-if --parameters "$parameter_file"
else
  echo "PASS local foundation tests complete; Azure state was not queried or changed."
  echo "Use --azure to add read-only provider, subscription, validation, and what-if checks."
fi