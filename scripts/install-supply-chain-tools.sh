#!/usr/bin/env bash
set -euo pipefail

tool_dir="${LEARNINGNEMO_TOOL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/learningnemo/bin}"
download_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$download_dir"
}
trap cleanup EXIT
mkdir -p "$tool_dir"

download() {
  local url="$1"
  local digest="$2"
  local output="$3"
  curl --fail --silent --show-error --location "$url" --output "$output"
  printf '%s  %s\n' "$digest" "$output" | sha256sum --check --status
}

syft_version="1.51.1"
syft_archive="$download_dir/syft.tar.gz"
download \
  "https://github.com/anchore/syft/releases/download/v$syft_version/syft_${syft_version}_linux_amd64.tar.gz" \
  "8fcb33017a0dc1058298c923c436d19dfa68ae93968e0b423248542e3afb9fc3" \
  "$syft_archive"
tar -xzf "$syft_archive" -C "$download_dir" syft
install -m 755 "$download_dir/syft" "$tool_dir/syft"

grype_version="0.118.0"
grype_archive="$download_dir/grype.tar.gz"
download \
  "https://github.com/anchore/grype/releases/download/v$grype_version/grype_${grype_version}_linux_amd64.tar.gz" \
  "1d444c5e7360471815f7158f71935fcecc68a3c417d85c7344f770854300bba2" \
  "$grype_archive"
tar -xzf "$grype_archive" -C "$download_dir" grype
install -m 755 "$download_dir/grype" "$tool_dir/grype"

cosign_version="3.1.3"
download \
  "https://github.com/sigstore/cosign/releases/download/v$cosign_version/cosign-linux-amd64" \
  "4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71" \
  "$download_dir/cosign"
install -m 755 "$download_dir/cosign" "$tool_dir/cosign"

crane_version="0.22.1"
crane_archive="$download_dir/crane.tar.gz"
download \
  "https://github.com/google/go-containerregistry/releases/download/v$crane_version/go-containerregistry_Linux_x86_64.tar.gz" \
  "0ab7a1d6932a213aed964ce97666c3077fe691c8606413674a8b3e0b9ec4cda0" \
  "$crane_archive"
tar -xzf "$crane_archive" -C "$download_dir" crane
install -m 755 "$download_dir/crane" "$tool_dir/crane"

"$tool_dir/syft" version >/dev/null
"$tool_dir/grype" version >/dev/null
"$tool_dir/cosign" version >/dev/null
"$tool_dir/crane" version >/dev/null
echo "PASS installed checksum-verified Syft, Grype, Cosign, and Crane"