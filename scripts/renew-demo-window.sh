#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "${LEARNINGNEMO_AZURE_APPLY:-}" == renew-demo-window ]]
[[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]
[[ "$(az account show --query id -o tsv)" == "$AZURE_SUBSCRIPTION_ID" ]]
mode="${1:---all}"
[[ "$mode" == --all || "$mode" == --dependencies-only ]]
artifact_scope="${2:---allow-human-services}"
[[ "$artifact_scope" == --allow-human-services || "$artifact_scope" == --allow-invoice-services ]]
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
if [[ -f "${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}/invoice-availability.policy.json" ]]; then
	printf 'Operator-managed invoice availability is configured; use configure_invoice_availability.py verify instead of timed renewal.\n' >&2
	exit 1
fi
LEARNINGNEMO_AZURE_APPLY=platform-runtime-envelope bash infra/next-phase/deploy-platform.sh --apply --ttl-hours 2
LEARNINGNEMO_AZURE_APPLY=wp3-artifacts bash infra/next-phase/deploy-artifacts.sh --apply --ttl-hours 2 "$artifact_scope"
LEARNINGNEMO_AZURE_APPLY=wp3-database-network bash infra/next-phase/deploy-database-network.sh --apply --ttl-hours 2
LEARNINGNEMO_AZURE_APPLY=renew-trusted-workers python3 infra/next-phase/renew_trusted_workers.py
if [[ "$mode" == --dependencies-only ]]; then
	printf 'PASS existing dependencies renewed; human release left unchanged\n'
	exit 0
fi
LEARNINGNEMO_AZURE_APPLY=human-handoff python3 infra/next-phase/deploy_human_services.py prepare
LEARNINGNEMO_AZURE_APPLY=human-handoff python3 infra/next-phase/deploy_human_services.py services
printf 'PASS existing demo dependencies renewed within two-hour window\n'