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
bash -n "$phase_dir/deploy-migration.sh" "$phase_dir/remove-migration.sh" "$phase_dir/test-migration.sh"
PYTHONPYCACHEPREFIX="$pycache" python3 -m py_compile \
  "$phase_dir/migration_contract.py" \
  "$phase_dir/migration_identity_parameters.py" \
  "$phase_dir/migration_parameters.py" \
  "$phase_dir/check_migration_cleanup.py" \
  "$phase_dir/preflight_migration.py" \
  "$phase_dir/record_migration.py" \
  "$phase_dir/summarize_migration_failure.py" \
  "$phase_dir/summarize_sql_admin_probe.py" \
  "$phase_dir/validate-database-regional-identity.py" \
  "$phase_dir/validate-migration.py" \
  "$phase_dir/verify_migration.py" \
  "$phase_dir/verify_migration_evidence.py" \
  "$phase_dir/verify_migration_identity.py" \
  "$phase_dir/wait_migration_execution.py" \
  "$project_dir/scripts/apply-sql-migrations.py"
az bicep build --file "$phase_dir/migration-stack.bicep" --stdout > "$compiled"
python3 "$phase_dir/validate-migration.py" "$compiled"
az bicep build --file "$phase_dir/database-migration-identity.bicep" --stdout > "$compiled"
python3 "$phase_dir/validate-migration-identity.py" "$compiled"
az bicep build --file "$phase_dir/database-regional-identity.bicep" --stdout > "$compiled"
python3 "$phase_dir/validate-database-regional-identity.py" "$compiled"
"$test_python" -m pytest -q \
  tests/test_migration_infra.py \
  tests/test_sql_migrations.py \
  tests/test_mssql_client.py
echo "PASS local hash-bound migration job gates complete; Azure was not queried or changed."