#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
bash -n infra/deploy-gateway.sh
bash -n infra/test-gateway-iac.sh
bash -n scripts/load-gateway-env.sh
bash -n scripts/run-agent.sh
bash -n scripts/test-gateway.sh
PYTHONPYCACHEPREFIX="$pycache_dir" python3 -m py_compile \
  infra/gateway_parameters.py \
  infra/summarize-gateway-what-if.py \
  infra/validate-gateway.py \
  scripts/test-semantic-guardrail.py \
  scripts/validate-agent-config.py
az bicep build --file infra/gateway.bicep --stdout > "$compiled_template"
python3 infra/validate-gateway.py "$compiled_template"
"$test_python" scripts/validate-agent-config.py
"$test_python" -m pytest -q tests/test_hybrid_guardrails.py tests/test_console.py
echo "PASS local hybrid guardrail and gateway IaC tests complete; Azure state was not queried or changed."