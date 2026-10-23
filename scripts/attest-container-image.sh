#!/usr/bin/env bash
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <artifact-prefix> <image-reference> <source-revision>" >&2
  exit 2
fi

prefix="$1"
image_reference="$2"
source_revision="$3"
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
tool_dir="${LEARNINGNEMO_TOOL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/learningnemo/bin}"

for tool in syft grype cosign; do
  if [[ ! -x "$tool_dir/$tool" ]]; then
    echo "Missing checksum-verified $tool in $tool_dir. Run scripts/install-supply-chain-tools.sh." >&2
    exit 2
  fi
done

mkdir -p "$state_dir"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

candidate_image="$work_dir/image.txt"
candidate_sbom="$work_dir/sbom.json"
candidate_scan="$work_dir/scan.json"
candidate_signature="$work_dir/signature.json"
candidate_release="$work_dir/release.json"
signature_bundle="$work_dir/signature-bundle.json"

printf '%s\n' "$image_reference" > "$candidate_image"
chmod 600 "$candidate_image"

"$tool_dir/syft" "$image_reference" --output "cyclonedx-json=$candidate_sbom" >/dev/null
"$tool_dir/grype" "$image_reference" --output json > "$candidate_scan"
chmod 600 "$candidate_sbom" "$candidate_scan"

key_prefix="$state_dir/trusted-runtime-cosign"
key_file="$key_prefix.key"
public_key="$key_prefix.pub"
password_file="$key_prefix.password"
if [[ ! -f "$key_file" || ! -f "$public_key" || ! -f "$password_file" ]]; then
  if [[ -e "$key_file" || -e "$public_key" || -e "$password_file" ]]; then
    echo "Signing key state is incomplete; refusing to replace a partial trust root." >&2
    exit 1
  fi
  python3 -c "import secrets; print(secrets.token_urlsafe(48))" > "$password_file"
  COSIGN_PASSWORD="$(cat "$password_file")" \
    "$tool_dir/cosign" generate-key-pair --output-key-prefix "$key_prefix" >/dev/null
  chmod 600 "$key_file" "$public_key" "$password_file"
fi

COSIGN_PASSWORD="$(cat "$password_file")" \
  "$tool_dir/cosign" sign-blob \
  --yes \
  --key "$key_file" \
  --bundle "$signature_bundle" \
  "$candidate_image" >/dev/null
"$tool_dir/cosign" verify-blob \
  --key "$public_key" \
  --bundle "$signature_bundle" \
  --insecure-ignore-tlog=true \
  "$candidate_image" >/dev/null

python3 - "$candidate_image" "$public_key" "$signature_bundle" "$candidate_signature" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

payload, public_key, bundle, output = map(Path, sys.argv[1:])
report = {
    "schemaVersion": 1,
    "verified": True,
    "scheme": "cosign-sign-blob",
    "imageReferenceSha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
    "publicKeySha256": hashlib.sha256(public_key.read_bytes()).hexdigest(),
    "bundle": json.loads(bundle.read_text(encoding="utf-8")),
}
output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(output, 0o600)
PY

PYTHONPATH="$phase_dir" python3 "$phase_dir/image_attestation.py" \
  --image-reference-file "$candidate_image" \
  --source-revision "$source_revision" \
  --sbom "$candidate_sbom" \
  --vulnerability-report "$candidate_scan" \
  --signature-verification "$candidate_signature" \
  --output "$candidate_release"

install -m 600 "$candidate_sbom" "$state_dir/$prefix.sbom.json"
install -m 600 "$candidate_scan" "$state_dir/$prefix.scan.json"
install -m 600 "$candidate_signature" "$state_dir/$prefix.signature.json"
install -m 600 "$candidate_release" "$state_dir/$prefix.release.json"
printf 'PASS %s supply-chain SBOM, scan, and Cosign attestation recorded\n' "$prefix"
