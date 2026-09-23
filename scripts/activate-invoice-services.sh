#!/usr/bin/env bash
set -euo pipefail
[[ "${LEARNINGNEMO_AZURE_APPLY:-}" == invoice-services ]]
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
python="/home/aygul/.venvs/nemo-agents/bin/python"
"$python" infra/next-phase/deploy_invoice_services.py prepare
"$python" infra/next-phase/deploy_invoice_services.py services
"$python" infra/next-phase/verify_invoice_services.py