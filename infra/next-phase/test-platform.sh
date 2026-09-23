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
parameter_file="$phase_dir/environments/dev.runtime.parameters.json"
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
bash -n "$phase_dir/deploy-platform.sh"
bash -n "$phase_dir/remove-platform.sh"
bash -n "$phase_dir/test-platform.sh"
PYTHONPYCACHEPREFIX="$pycache_dir" python3 -m py_compile \
  "$phase_dir/check_platform_cleanup.py" \
  "$phase_dir/platform_contract.py" \
  "$phase_dir/platform_parameters.py" \
  "$phase_dir/preflight_platform.py" \
  "$phase_dir/record_platform.py" \
  "$phase_dir/runtime_parameters.py" \
  "$phase_dir/summarize_what_if.py" \
  "$phase_dir/validate-platform.py" \
  "$phase_dir/verify_platform.py"
python3 "$phase_dir/runtime_parameters.py" validate "$parameter_file"
python3 "$phase_dir/preflight_platform.py" --offline --parameters "$parameter_file"
az bicep build --file "$phase_dir/platform.bicep" --stdout > "$compiled_template"
python3 "$phase_dir/validate-platform.py" "$compiled_template"
"$test_python" -m pytest -q tests/test_next_phase_platform_infra.py

if [[ "$azure" == true ]]; then
  bash "$phase_dir/register-providers.sh" --phase platform
  bash "$phase_dir/deploy-platform.sh" --what-if --parameters "$parameter_file"
else
  echo "PASS local WP2a tests complete; Azure state was not queried or changed."
  echo "Use --azure to add read-only provider, subscription, validation, and what-if checks."
fi