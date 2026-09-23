#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
settings_path="${LEARNINGNEMO_SETTINGS:-$repo_root/.nemo-test-client.json}"
nat_bin="${NAT_BIN:-/home/aygul/.venvs/nemo-agents/bin/nat}"

if [[ ! -f "$settings_path" ]]; then
  echo "Missing non-secret settings file: $settings_path" >&2
  exit 1
fi

mapfile -t entra_settings < <(python3 - "$settings_path" <<'PY'
import json
import sys

settings = json.load(open(sys.argv[1], encoding="utf-8"))
for name in ("ENTRA_TENANT_ID", "ENTRA_CLIENT_ID", "ENTRA_PUBLIC_CLIENT_ID"):
    value = settings.get(name)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"Missing {name} in settings file")
    print(value)
PY
)

export ENTRA_TENANT_ID="${entra_settings[0]}"
export ENTRA_CLIENT_ID="${entra_settings[1]}"
export ENTRA_PUBLIC_CLIENT_ID="${entra_settings[2]}"
source "$repo_root/scripts/load-gateway-env.sh" >/dev/null
export NAT_TELEMETRY_ENABLED=false

cd "$repo_root"
exec "$nat_bin" serve --config_file configs/agent.yml "$@"