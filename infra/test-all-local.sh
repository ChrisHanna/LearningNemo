#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gates=(
  "infra/test-gateway-iac.sh"
  "infra/next-phase/test-foundation.sh"
  "infra/next-phase/test-budget.sh"
  "infra/next-phase/test-identities.sh"
  "infra/next-phase/test-platform.sh"
  "infra/next-phase/test-entra-workloads.sh"
  "infra/next-phase/test-workloads.sh"
  "infra/next-phase/test-database.sh"
  "infra/next-phase/test-artifacts.sh"
  "infra/next-phase/test-migration.sh"
)

cd "$project_dir"
for gate in "${gates[@]}"; do
  echo "Running $gate"
  bash "$gate"
done
bash -n \
  infra/next-phase/reconcile-wp3.sh \
  infra/next-phase/verify-wp3.sh
echo "PASS all local LearningNeMo infrastructure gates complete; Azure state was not queried or changed."