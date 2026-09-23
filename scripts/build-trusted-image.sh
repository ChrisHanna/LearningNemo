#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
phase_dir="$project_dir/infra/next-phase"
state_dir="${LEARNINGNEMO_INFRA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo}"
tool_dir="${LEARNINGNEMO_TOOL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/learningnemo/bin}"
environment="${LEARNINGNEMO_ENVIRONMENT:-dev}"
artifact_state="${ARTIFACT_PRIVATE_STATE_FILE:-$state_dir/artifacts-$environment.state.json}"
base_images="$project_dir/containers/trusted-runtime.base-images.json"
repository="learningnemo/trusted-runtime"

require_private_file() {
  local label="$1"
  local path="$2"
  if [[ ! -f "$path" ]]; then
    echo "$label must name an existing private file." >&2
    exit 2
  fi
  local permissions
  permissions="$(stat -c '%a' "$path")"
  if (( (8#$permissions & 077) != 0 )); then
    echo "$label must be readable and writable only by its owner." >&2
    exit 2
  fi
}

require_private_file "ARTIFACT_PRIVATE_STATE_FILE" "$artifact_state"
for tool in syft grype cosign crane; do
  if [[ ! -x "$tool_dir/$tool" ]]; then
    echo "Missing checksum-verified $tool. Run scripts/install-supply-chain-tools.sh." >&2
    exit 2
  fi
done

read_json() {
  python3 - "$1" "$2" <<'PY'
import json
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))[sys.argv[2]]
if not isinstance(value, str) or not value:
    raise SystemExit("requested JSON value is not a string")
print(value)
PY
}
registry_name="$(read_json "$artifact_state" registryName)"
login_server="$(read_json "$artifact_state" loginServer)"
build_base="$(read_json "$base_images" build)"
native_build_base="$(read_json "$base_images" nativeBuild)"
runtime_base="$(read_json "$base_images" runtime)"
image_pattern='^[a-z0-9./:_-]+@sha256:[0-9a-f]{64}$'
if ! [[ "$build_base" =~ $image_pattern && "$native_build_base" =~ $image_pattern && "$runtime_base" =~ $image_pattern ]]; then
  echo "Trusted base images must be immutable digest references." >&2
  exit 1
fi

mkdir -p "$state_dir"
source_revision="$(PYTHONPATH="$phase_dir" python3 - "$project_dir" <<'PY'
import sys
from pathlib import Path
from trusted_image_release import source_revision
print(source_revision(Path(sys.argv[1])))
PY
)"
tag="source-${source_revision:0:16}"
build_log="$(mktemp)"
docker_config="$(mktemp -d)"
release_stage="$(mktemp -d "$state_dir/.trusted-runtime-$environment.XXXXXX")"
cleanup() {
  rm -f "$build_log"
  rm -rf "$docker_config" "$release_stage"
}
trap cleanup EXIT

echo "Building the trusted runtime remotely from its hash-locked context."
az acr build \
  --registry "$registry_name" \
  --image "$repository:$tag" \
  --file "containers/trusted-runtime.Dockerfile" \
  --platform linux/amd64 \
  --build-arg "BUILD_BASE_IMAGE=$build_base" \
  --build-arg "NATIVE_BUILD_BASE_IMAGE=$native_build_base" \
  --build-arg "RUNTIME_BASE_IMAGE=$runtime_base" \
  "$project_dir" > "$build_log"

token="$(az acr login --name "$registry_name" --expose-token --query accessToken --output tsv)"
if [[ -z "$token" ]]; then
  echo "Unable to acquire an ephemeral ACR data-plane token." >&2
  exit 1
fi
export DOCKER_CONFIG="$docker_config"
printf '%s' "$token" | "$tool_dir/crane" auth login \
  "$login_server" \
  --username 00000000-0000-0000-0000-000000000000 \
  --password-stdin >/dev/null
unset token
digest="$("$tool_dir/crane" digest "$login_server/$repository:$tag")"
if ! [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "Remote build did not produce one immutable digest." >&2
  exit 1
fi
image_reference="$login_server/$repository@$digest"
current_source_revision="$(PYTHONPATH="$phase_dir" python3 - "$project_dir" <<'PY'
import sys
from pathlib import Path
from trusted_image_release import source_revision
print(source_revision(Path(sys.argv[1])))
PY
)"
if [[ "$current_source_revision" != "$source_revision" ]]; then
  echo "Trusted image source changed during the remote build; release evidence was not generated." >&2
  exit 1
fi

image_file="$state_dir/trusted-runtime-$environment.image.txt"
sbom_file="$state_dir/trusted-runtime-$environment.sbom.json"
scan_file="$state_dir/trusted-runtime-$environment.scan.json"
image_archive="$docker_config/trusted-runtime.tar"
signature_file="$state_dir/trusted-runtime-$environment.signature.json"
approval_schema="$state_dir/trusted-runtime-$environment.approval-schema.sql"
release_file="$state_dir/trusted-runtime-$environment.release.json"
candidate_image_file="$release_stage/image.txt"
candidate_sbom_file="$release_stage/sbom.json"
candidate_scan_file="$release_stage/scan.json"
candidate_signature_file="$release_stage/signature.json"
candidate_approval_schema="$release_stage/approval-schema.sql"
candidate_release_file="$release_stage/release.json"
printf '%s\n' "$image_reference" > "$candidate_image_file"
chmod 600 "$candidate_image_file"
install -m 600 "$phase_dir/sql/001_schema.sql" "$candidate_approval_schema"

"$tool_dir/syft" "$image_reference" --output "cyclonedx-json=$candidate_sbom_file" >/dev/null
"$tool_dir/grype" "$image_reference" --output json > "$candidate_scan_file"
chmod 600 "$candidate_sbom_file" "$candidate_scan_file"
"$tool_dir/crane" export --platform linux/amd64 "$image_reference" "$image_archive"
python3 "$phase_dir/verify_image_elf.py" "$image_archive"

key_prefix="$state_dir/trusted-runtime-cosign"
key_file="$key_prefix.key"
public_key="$key_prefix.pub"
password_file="$key_prefix.password"
if [[ ! -f "$key_file" || ! -f "$public_key" || ! -f "$password_file" ]]; then
  if [[ -e "$key_file" || -e "$public_key" || -e "$password_file" ]]; then
    echo "Signing key state is incomplete; refusing to replace a partial trust root." >&2
    exit 1
  fi
  umask 077
  python3 -c "import secrets; print(secrets.token_urlsafe(48))" > "$password_file"
  COSIGN_PASSWORD="$(cat "$password_file")" \
    "$tool_dir/cosign" generate-key-pair --output-key-prefix "$key_prefix" >/dev/null
  chmod 600 "$key_file" "$public_key" "$password_file"
fi
require_private_file "Cosign private key" "$key_file"
require_private_file "Cosign key password" "$password_file"

signature_payload="$docker_config/signature-payload.txt"
signature_bundle="$docker_config/signature-bundle.json"
printf '%s\n' "$image_reference" > "$signature_payload"
COSIGN_PASSWORD="$(cat "$password_file")" \
  "$tool_dir/cosign" sign-blob \
  --yes \
  --key "$key_file" \
  --bundle "$signature_bundle" \
  "$signature_payload" >/dev/null
"$tool_dir/cosign" verify-blob \
  --key "$public_key" \
  --bundle "$signature_bundle" \
  --insecure-ignore-tlog=true \
  "$signature_payload" >/dev/null
python3 - "$signature_payload" "$public_key" "$signature_bundle" "$candidate_signature_file" <<'PY'
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
chmod 600 "$candidate_signature_file"

python3 "$phase_dir/trusted_image_release.py" \
  --root "$project_dir" \
  --image-reference-file "$candidate_image_file" \
  --sbom "$candidate_sbom_file" \
  --vulnerability-report "$candidate_scan_file" \
  --signature-verification "$candidate_signature_file" \
  --approval-schema "$candidate_approval_schema" \
  --output "$candidate_release_file"
python3 "$phase_dir/workload_release.py" \
  --attestation "$candidate_release_file" \
  --image-reference-file "$candidate_image_file" \
  --sbom "$candidate_sbom_file" \
  --vulnerability-report "$candidate_scan_file" \
  --signature-verification "$candidate_signature_file" \
  --approval-schema "$candidate_approval_schema" \
  --source-root "$project_dir"
mv -f "$candidate_image_file" "$image_file"
mv -f "$candidate_sbom_file" "$sbom_file"
mv -f "$candidate_scan_file" "$scan_file"
mv -f "$candidate_signature_file" "$signature_file"
mv -f "$candidate_approval_schema" "$approval_schema"
mv -f "$candidate_release_file" "$release_file"
echo "PASS trusted runtime digest built, scanned, signed, verified, and recorded in owner-only state"