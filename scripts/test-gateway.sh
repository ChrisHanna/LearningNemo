#!/usr/bin/env bash
set -euo pipefail

key_vault="${AZURE_KEY_VAULT_NAME:-kvnemo8370187d}"
gateway_url="${OPENAI_BASE_URL:-https://apim-nemo-8370187d.azure-api.net/llm/v1}"
guardrail_url="${OPENAI_GUARDRAIL_BASE_URL:-${gateway_url}/guardrails}"

gateway_key="${OPENAI_API_KEY:-$(az keyvault secret show \
  --vault-name "$key_vault" \
  --name llm-gateway-client-key \
  --query value \
  --output tsv)}"
work_dir="$(mktemp -d)"

cleanup() {
  unset gateway_key
  rm -rf "$work_dir"
}
trap cleanup EXIT

call_gateway() {
  local label="$1"
  local url="$2"
  local expected="$3"
  local response_file="$work_dir/$label-response.json"
  local headers_file="$work_dir/$label-headers.txt"

  curl --silent --show-error \
    --dump-header "$headers_file" \
    --output "$response_file" \
    --header "Authorization: Bearer $gateway_key" \
    --header "Content-Type: application/json" \
    --data "{\"model\":\"gpt-4o-mini\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: $expected\"}],\"max_tokens\":10,\"temperature\":0}" \
    "$url/chat/completions"

  python3 - "$label" "$headers_file" "$response_file" "$expected" <<'PY'
import json
import sys
from pathlib import Path

label = sys.argv[1]
headers = Path(sys.argv[2]).read_text(errors="replace")
body = Path(sys.argv[3]).read_text(errors="replace")
expected = sys.argv[4]
status_line = headers.splitlines()[0] if headers.splitlines() else "missing"
content_type = next(
    (line.split(":", 1)[1].strip() for line in headers.splitlines() if line.lower().startswith("content-type:")),
    "missing",
)

print(f"{label.upper()}_STATUS_LINE={status_line}")
print(f"{label.upper()}_CONTENT_TYPE={content_type}")
print(f"{label.upper()}_BODY_BYTES={len(body.encode())}")

try:
    payload = json.loads(body)
except json.JSONDecodeError:
    print(f"{label.upper()}_BODY_JSON=false")
    raise SystemExit(1)

print(f"{label.upper()}_BODY_JSON=true")
print(f"{label.upper()}_MODEL={payload.get('model', 'missing')}")
choices = payload.get("choices") or []
content = (choices[0].get("message") or {}).get("content") if choices else None
print(f"{label.upper()}_CONTENT_MATCH={isinstance(content, str) and expected in content.lower()}")

error = payload.get("error") or {}
if error:
    print(f"{label.upper()}_ERROR_CODE={error.get('code')}")
    raise SystemExit(1)

if not isinstance(content, str) or expected not in content.lower():
    raise SystemExit(1)
PY
}

require_unauthorized() {
  local label="$1"
  local url="$2"
  local status
  status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
    --header "Authorization: Bearer invalid" \
    --header "Content-Type: application/json" \
    --data '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"test"}]}' \
    "$url/chat/completions")"
  if [[ "$status" != "401" ]]; then
    echo "${label^^}_UNAUTHORIZED_STATUS=$status" >&2
    return 1
  fi
  echo "${label^^}_UNAUTHORIZED_STATUS=401"
}

call_gateway "agent" "$gateway_url" "gateway-ok"
call_gateway "guardrail" "$guardrail_url" "no"
require_unauthorized "agent" "$gateway_url"
require_unauthorized "guardrail" "$guardrail_url"