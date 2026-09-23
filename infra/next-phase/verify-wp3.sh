#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
environment="${LEARNINGNEMO_ENVIRONMENT:-dev}"

if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  echo "Verification blocked. Set AZURE_SUBSCRIPTION_ID to the expected subscription." >&2
  exit 2
fi
if [[ "$(az account show --query id --output tsv)" != "$AZURE_SUBSCRIPTION_ID" ]]; then
  echo "Verification blocked: active subscription differs." >&2
  exit 1
fi

python3 "$phase_dir/verify_database.py" \
  --parameters "$phase_dir/environments/dev.database.parameters.json"
python3 "$phase_dir/verify_database_network.py" \
  --parameters "$state_dir/database-network-$environment.parameters.json"
python3 "$phase_dir/verify_artifacts.py" \
  --parameters "$state_dir/artifacts-$environment.parameters.json"
python3 "$phase_dir/verify_platform.py" \
  --parameters "$state_dir/runtime-$environment.parameters.json" \
  --allow-workloads
python3 "$phase_dir/workload_release.py" \
  --attestation "$state_dir/trusted-runtime-$environment.release.json" \
  --image-reference-file "$state_dir/trusted-runtime-$environment.image.txt" \
  --sbom "$state_dir/trusted-runtime-$environment.sbom.json" \
  --vulnerability-report "$state_dir/trusted-runtime-$environment.scan.json" \
  --signature-verification "$state_dir/trusted-runtime-$environment.signature.json" \
  --approval-schema "$state_dir/trusted-runtime-$environment.approval-schema.sql" \
  --source-root "$project_dir"
python3 "$phase_dir/verify_migration_evidence.py" \
  --manifest "$state_dir/migration-$environment.manifest.json" \
  --config "$phase_dir/environments/dev.migration.config.json" \
  --release-attestation "$state_dir/trusted-runtime-$environment.release.json" \
  --image-reference-file "$state_dir/trusted-runtime-$environment.image.txt"
python3 "$phase_dir/verify_workloads.py" \
  --config "$phase_dir/environments/dev.workloads.config.json" \
  --parameters "$state_dir/workloads-$environment.parameters.json"
echo "PASS complete WP3 SQL, image, migration, and worker deployment matches recorded IaC state."