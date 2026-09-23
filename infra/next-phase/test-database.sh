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
parameters="$phase_dir/environments/dev.database.parameters.json"
platform_parameters="$phase_dir/environments/dev.platform.parameters.json"
compiled_base="$(mktemp)"
compiled_network="$(mktemp)"
pycache_dir="$(mktemp -d)"
test_python="${LEARNINGNEMO_TEST_PYTHON:-${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/nemo-agents}/bin/python}"
cleanup() {
  rm -f "$compiled_base" "$compiled_network"
  rm -rf "$pycache_dir"
}
trap cleanup EXIT

cd "$project_dir"
python3 "$phase_dir/check_toolchain.py"
bash -n "$phase_dir/deploy-database.sh"
bash -n "$phase_dir/deploy-database-network.sh"
bash -n "$phase_dir/remove-database-network.sh"
bash -n "$phase_dir/remove-database.sh"
bash -n "$phase_dir/test-database.sh"
PYTHONPYCACHEPREFIX="$pycache_dir" python3 -m py_compile \
  "$phase_dir/check_database_cleanup.py" \
  "$phase_dir/check_database_network_cleanup.py" \
  "$phase_dir/database_contract.py" \
  "$phase_dir/database_network_contract.py" \
  "$phase_dir/database_network_parameters.py" \
  "$phase_dir/database_parameters.py" \
  "$phase_dir/preflight_database_network.py" \
  "$phase_dir/preflight_database.py" \
  "$phase_dir/record_database.py" \
  "$phase_dir/record_database_network.py" \
  "$phase_dir/sql_migrations.py" \
  "$phase_dir/summarize_database_what_if.py" \
  "$phase_dir/validate-database.py" \
  "$phase_dir/validate-sql.py" \
  "$phase_dir/verify_database_network.py" \
  "$phase_dir/verify_database.py"
python3 "$phase_dir/database_parameters.py" "$parameters"
python3 "$phase_dir/preflight_database.py" \
  --offline \
  --parameters "$parameters" \
  --platform-parameters "$platform_parameters"
python3 "$phase_dir/validate-sql.py"
az bicep build --file "$phase_dir/database-stack.bicep" --stdout > "$compiled_base"
az bicep build --file "$phase_dir/database-network.bicep" --stdout > "$compiled_network"
python3 "$phase_dir/validate-database.py" "$compiled_base" "$compiled_network"
"$test_python" -m pytest -q \
  tests/test_database_infra.py \
  tests/test_sql_migrations.py \
  tests/test_sql_adapters.py \
  tests/test_mssql_client.py \
  tests/test_trusted_runtime.py \
  tests/test_trusted_workflow.py \
  tests/test_mssql_owned_query.py

if [[ "$azure" == true ]]; then
  bash "$phase_dir/register-providers.sh" --phase database
  bash "$phase_dir/deploy-database.sh" --what-if
else
  echo "PASS local WP3 database tests complete; Azure state was not queried or changed."
fi