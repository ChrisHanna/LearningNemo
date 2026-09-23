#!/usr/bin/env python3
"""Capture owner-only, sanitized diagnostics before preserving a failed SAW VM."""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from summarize_workspace_failure import WorkspaceFailureSummaryError
from summarize_workspace_failure import latest_deployment
from summarize_workspace_failure import summarize


DIAGNOSTIC_ARCHIVE = "/var/lib/learningnemo-saw/diagnostics.txt.gz"
DIAGNOSTIC_CHUNK_BYTES = 2048
MAX_DIAGNOSTIC_ARCHIVE_BYTES = 65536
MAX_DIAGNOSTIC_TEXT_BYTES = 1048576
MANIFEST_BEGIN = "BEGIN_WORKSPACE_DIAGNOSTIC_MANIFEST"
MANIFEST_END = "END_WORKSPACE_DIAGNOSTIC_MANIFEST"
CHUNK_BEGIN = "BEGIN_WORKSPACE_DIAGNOSTIC_CHUNK"
CHUNK_END = "END_WORKSPACE_DIAGNOSTIC_CHUNK"

DIAGNOSTIC_SCRIPT = r"""#!/usr/bin/env bash
set -Eeuo pipefail
admin_user="sawadmin"
admin_uid="$(id -u "$admin_user" 2>/dev/null)"
admin_home="$(getent passwd "$admin_user" 2>/dev/null | cut -d: -f6)"
runtime_dir="/run/user/$admin_uid"
state_dir="/var/lib/learningnemo-saw"
diagnostic_text="$state_dir/diagnostics.txt"
diagnostic_archive="$state_dir/diagnostics.txt.gz"
raw_file="$(mktemp "$state_dir/diagnostics.raw.XXXXXX")"
text_file="$(mktemp "$state_dir/diagnostics.text.XXXXXX")"
archive_file="$(mktemp "$state_dir/diagnostics.archive.XXXXXX")"
trap 'rm -f "$raw_file" "$text_file" "$archive_file"' EXIT
sanitize() {
  sed -E \
    -e 's#https?://[^[:space:]]+#<url>#g' \
    -e 's/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F-]{20,}/<uuid>/g' \
    -e 's/[0-9a-fA-F]{40,}/<digest>/g' \
    -e 's/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+/<email>/g' \
    -e 's/([Tt]oken|[Ss]ecret|[Pp]assword|[Cc]redential|[Pp]rivate[Kk]ey|[Aa]pi[Kk]ey)[=:][^[:space:]]+/\1=<redacted>/g' \
    -e 's#(/subscriptions/)[^[:space:]]+#\1<redacted>#g' \
    -e 's#([A-Za-z0-9-]+\.){2,}[A-Za-z]{2,}#<host>#g' \
    -e 's/([0-9]{1,3}\.){3}[0-9]{1,3}/<ip>/g'
}
run_user() {
  runuser -u "$admin_user" -- env \
    HOME="$admin_home" \
    XDG_RUNTIME_DIR="$runtime_dir" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_dir/bus" \
    "$@"
}
collect() (
    set +e
    echo BEGIN_WORKSPACE_DIAGNOSTICS
    echo "captured_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "admin_uid=${admin_uid:-unavailable}"
    echo "admin_groups=$(id -Gn "$admin_user" 2>/dev/null | tr ' ' ',' || echo unavailable)"
    echo "kvm=$(stat -c 'mode=%a,owner=%U,group=%G' /dev/kvm 2>/dev/null || echo absent)"
    echo "openshell_package=$(dpkg-query -W -f='${Status}|${Version}' openshell 2>/dev/null || echo absent)"
    echo "openshell_cli=$(/usr/bin/openshell --version 2>/dev/null | head -n 1 || echo unavailable)"
    echo "gateway_config=$(stat -c 'mode=%a,owner=%U,group=%G,size=%s' "$admin_home/.config/openshell/gateway.toml" 2>/dev/null || echo absent)"
    echo "gateway_config_sha256=$(sha256sum "$admin_home/.config/openshell/gateway.toml" 2>/dev/null | cut -d' ' -f1 || echo absent)"
    echo "state_tree=$(stat -c 'mode=%a,owner=%U,group=%G' "$admin_home/.local/state/openshell" 2>/dev/null || echo absent)"
    echo "driver=$(stat -c 'mode=%a,owner=%U,group=%G,size=%s' /usr/libexec/openshell/openshell-driver-vm 2>/dev/null || echo absent)"
    echo "user_manager=$(loginctl show-user "$admin_user" -p State -p Linger --value 2>/dev/null | paste -sd, - || echo unavailable)"
    echo BEGIN_SYSTEMD_PROPERTIES
    run_user systemctl --user show openshell-gateway.service \
        -p LoadState -p ActiveState -p SubState -p Result -p ExecMainCode -p ExecMainStatus -p NRestarts \
        --no-pager 2>&1
    echo END_SYSTEMD_PROPERTIES
    echo BEGIN_GATEWAY_JOURNAL
    run_user journalctl --user -u openshell-gateway.service --no-pager -n 160 -o short-monotonic 2>&1
    echo END_GATEWAY_JOURNAL
    echo BEGIN_BOOTSTRAP_LOG
    tail -n 240 /var/lib/learningnemo-saw/bootstrap.log 2>&1
    echo END_BOOTSTRAP_LOG
    echo BEGIN_STATE_FILES
    find "$admin_home/.local/state/openshell" -maxdepth 6 -type f \
        -printf '%P|%m|%u|%g|%s\n' 2>/dev/null | sort | tail -n 160
    echo END_STATE_FILES
    echo BEGIN_VM_CONSOLES
    while IFS= read -r console; do
        echo BEGIN_VM_CONSOLE
        stat -c 'mode=%a,owner=%U,group=%G,size=%s' "$console" 2>/dev/null || true
        tail -n 240 "$console" 2>/dev/null || true
        echo END_VM_CONSOLE
    done < <(
        find "$admin_home/.local/state/openshell/vm" -maxdepth 4 -type f \
            -name 'rootfs-console.log' -print 2>/dev/null | sort | tail -n 3
    )
    echo END_VM_CONSOLES
    echo BEGIN_BOOTSTRAP_FILES
    find /var/lib/learningnemo-saw -maxdepth 2 -type f \
        -printf '%P|%m|%u|%g|%s\n' 2>/dev/null | sort
    echo END_BOOTSTRAP_FILES
    echo END_WORKSPACE_DIAGNOSTICS
)
collect > "$raw_file" 2>&1
sanitize < "$raw_file" > "$text_file"
chmod 0600 "$text_file"
mv -f "$text_file" "$diagnostic_text"
python3 - "$diagnostic_text" "$archive_file" <<'PY'
import gzip
import shutil
import sys

with open(sys.argv[1], "rb") as source, open(sys.argv[2], "wb") as target:
        with gzip.GzipFile(fileobj=target, mode="wb", mtime=0) as compressed:
                shutil.copyfileobj(source, compressed)
PY
chmod 0600 "$archive_file"
mv -f "$archive_file" "$diagnostic_archive"
archive_bytes="$(stat -c %s "$diagnostic_archive")"
archive_sha256="$(sha256sum "$diagnostic_archive" | cut -d' ' -f1)"
printf 'BEGIN_WORKSPACE_DIAGNOSTIC_MANIFEST\nbytes=%s\nsha256=%s\nEND_WORKSPACE_DIAGNOSTIC_MANIFEST\n' \
    "$archive_bytes" "$archive_sha256"
"""

UUID_SEARCH = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)
IP_SEARCH = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
URL_SEARCH = re.compile(r"https?://\S+", re.IGNORECASE)
DIGEST_SEARCH = re.compile(r"(?<![0-9a-f])[0-9a-f]{40,}(?![0-9a-f])", re.IGNORECASE)
EMAIL_SEARCH = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")
FQDN_SEARCH = re.compile(r"\b(?:[A-Za-z0-9-]+\.){2,}[A-Za-z]{2,}\b")
CREDENTIAL_SEARCH = re.compile(
    r"(?i)(token|secret|password|credential|privatekey|apikey)[=:](?!<redacted>)\S+"
)
SUBSCRIPTION_SEARCH = re.compile(r"(?i)(/subscriptions/)(?!<redacted>)\S+")
MANIFEST_PATTERN = re.compile(
    rf"{MANIFEST_BEGIN}\r?\nbytes=([0-9]+)\r?\nsha256=([0-9a-f]{{64}})\r?\n{MANIFEST_END}",
    re.IGNORECASE,
)
CHUNK_PATTERN = re.compile(
    rf"{CHUNK_BEGIN}\r?\nindex=([0-9]+)\r?\ndata=([A-Za-z0-9+/=]+)\r?\n{CHUNK_END}"
)


class WorkspaceDiagnosticError(RuntimeError):
    pass


def az_json(arguments: list[str], *, timeout: int = 300) -> Any:
    try:
        result = subprocess.run(
            ["az", *arguments, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkspaceDiagnosticError("workspace diagnostic query failed or timed out") from error
    if result.returncode != 0:
        raise WorkspaceDiagnosticError("workspace diagnostic query failed")
    try:
        return json.loads(result.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise WorkspaceDiagnosticError("workspace diagnostic query returned unreadable JSON") from error


def string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in string_values(child)]
    if isinstance(value, list):
        return [text for child in value for text in string_values(child)]
    return []


def extract_marked_text(response: Any, begin: str, end: str, label: str) -> str:
    candidates = [text for text in string_values(response) if begin in text]
    if len(candidates) != 1:
        raise WorkspaceDiagnosticError(f"workspace diagnostic {label} marker differs")
    text = candidates[0]
    if end not in text:
        raise WorkspaceDiagnosticError(f"workspace diagnostic {label} is incomplete")
    return text[text.index(begin) : text.index(end) + len(end)]


def validate_diagnostic_text(text: str) -> str:
    normalized = text.rstrip("\r\n")
    if not normalized.startswith("BEGIN_WORKSPACE_DIAGNOSTICS") or not normalized.endswith(
        "END_WORKSPACE_DIAGNOSTICS"
    ):
        raise WorkspaceDiagnosticError("workspace diagnostic output marker differs")
    prohibited = (
        UUID_SEARCH,
        IP_SEARCH,
        URL_SEARCH,
        DIGEST_SEARCH,
        EMAIL_SEARCH,
        FQDN_SEARCH,
        CREDENTIAL_SEARCH,
        SUBSCRIPTION_SEARCH,
    )
    if any(pattern.search(normalized) for pattern in prohibited):
        raise WorkspaceDiagnosticError("workspace diagnostic output contains an unsanitized identifier")
    if any(marker in normalized.casefold() for marker in ("private key", "ssh-rsa", "ssh-ed25519", "bearer ")):
        raise WorkspaceDiagnosticError("workspace diagnostic output contains prohibited material")
    return normalized


def extract_diagnostic_text(response: Any) -> str:
    text = extract_marked_text(
        response,
        "BEGIN_WORKSPACE_DIAGNOSTICS",
        "END_WORKSPACE_DIAGNOSTICS",
        "output",
    )
    return validate_diagnostic_text(text)


def parse_manifest(response: Any) -> tuple[int, str]:
    text = extract_marked_text(response, MANIFEST_BEGIN, MANIFEST_END, "manifest")
    match = MANIFEST_PATTERN.fullmatch(text)
    if match is None:
        raise WorkspaceDiagnosticError("workspace diagnostic manifest differs")
    size = int(match.group(1))
    if size <= 0 or size > MAX_DIAGNOSTIC_ARCHIVE_BYTES:
        raise WorkspaceDiagnosticError("workspace diagnostic archive size differs")
    return size, match.group(2).lower()


def chunk_script(index: int, offset: int, count: int) -> str:
    return f"""#!/usr/bin/env bash
set -Eeuo pipefail
archive={DIAGNOSTIC_ARCHIVE!r}
[[ -f "$archive" ]]
[[ "$(stat -c %s "$archive")" -ge {offset + count} ]]
printf '{CHUNK_BEGIN}\\nindex={index}\\ndata='
dd if="$archive" bs=1 skip={offset} count={count} status=none | base64 -w0
printf '\\n{CHUNK_END}\\n'
"""


def parse_chunk(response: Any, expected_index: int, expected_size: int) -> bytes:
    text = extract_marked_text(response, CHUNK_BEGIN, CHUNK_END, "chunk")
    match = CHUNK_PATTERN.fullmatch(text)
    if match is None or int(match.group(1)) != expected_index:
        raise WorkspaceDiagnosticError("workspace diagnostic chunk differs")
    try:
        value = base64.b64decode(match.group(2), validate=True)
    except (ValueError, binascii.Error) as error:
        raise WorkspaceDiagnosticError("workspace diagnostic chunk is unreadable") from error
    if len(value) != expected_size:
        raise WorkspaceDiagnosticError("workspace diagnostic chunk length differs")
    return value


def invoke_script(resource_group: str, vm_name: str, script: str) -> Any:
    return az_json(
        [
            "vm",
            "run-command",
            "invoke",
            "--resource-group",
            resource_group,
            "--name",
            vm_name,
            "--command-id",
            "RunShellScript",
            "--scripts",
            script,
        ],
        timeout=300,
    )


def retrieve_diagnostic_text(resource_group: str, vm_name: str) -> str:
    archive_size, expected_sha256 = parse_manifest(
        invoke_script(resource_group, vm_name, DIAGNOSTIC_SCRIPT)
    )
    chunks: list[bytes] = []
    for index, offset in enumerate(range(0, archive_size, DIAGNOSTIC_CHUNK_BYTES)):
        count = min(DIAGNOSTIC_CHUNK_BYTES, archive_size - offset)
        response = invoke_script(resource_group, vm_name, chunk_script(index, offset, count))
        chunks.append(parse_chunk(response, index, count))
    archive = b"".join(chunks)
    if len(archive) != archive_size or hashlib.sha256(archive).hexdigest() != expected_sha256:
        raise WorkspaceDiagnosticError("workspace diagnostic archive integrity differs")
    try:
        payload = gzip.decompress(archive)
        if len(payload) > MAX_DIAGNOSTIC_TEXT_BYTES:
            raise WorkspaceDiagnosticError("workspace diagnostic text is too large")
        text = payload.decode("utf-8")
    except (gzip.BadGzipFile, OSError, UnicodeDecodeError) as error:
        raise WorkspaceDiagnosticError("workspace diagnostic archive is unreadable") from error
    return validate_diagnostic_text(text)


def redact_text(text: str) -> str:
    text = URL_SEARCH.sub("<url>", text)
    text = UUID_SEARCH.sub("<uuid>", text)
    text = DIGEST_SEARCH.sub("<digest>", text)
    text = EMAIL_SEARCH.sub("<email>", text)
    text = SUBSCRIPTION_SEARCH.sub(r"\1<redacted>", text)
    text = FQDN_SEARCH.sub("<host>", text)
    text = IP_SEARCH.sub("<ip>", text)
    return CREDENTIAL_SEARCH.sub(lambda match: f"{match.group(1)}=<redacted>", text)


def validate_report(report: dict[str, Any]) -> None:
    prohibited = (
        UUID_SEARCH,
        IP_SEARCH,
        URL_SEARCH,
        DIGEST_SEARCH,
        EMAIL_SEARCH,
        FQDN_SEARCH,
        CREDENTIAL_SEARCH,
        SUBSCRIPTION_SEARCH,
    )
    if any(pattern.search(value) for value in string_values(report) for pattern in prohibited):
        raise WorkspaceDiagnosticError("workspace diagnostic report contains an unsanitized identifier")


def write_private(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--vm-name", required=True)
    parser.add_argument("--deployment-prefix", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        arm_passed: set[str] = set()
        arm_failed: set[str] = set()
        arm_codes: set[str] = set()
        try:
            _name, deployment = latest_deployment(args.resource_group, args.deployment_prefix)
            arm_passed, arm_failed, arm_codes = summarize(deployment)
        except WorkspaceFailureSummaryError:
            pass
        diagnostic_text = retrieve_diagnostic_text(args.resource_group, args.vm_name)
        boot_log = ""
        try:
            boot_response = subprocess.run(
                [
                    "az",
                    "vm",
                    "boot-diagnostics",
                    "get-boot-log",
                    "--resource-group",
                    args.resource_group,
                    "--name",
                    args.vm_name,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if boot_response.returncode == 0:
                safe_lines = [
                    line
                    for line in boot_response.stdout.splitlines()
                    if re.search(r"cloud-init|waagent|systemd|failed|error", line, re.IGNORECASE)
                ][-120:]
                boot_log = redact_text("\n".join(safe_lines))
        except (OSError, subprocess.TimeoutExpired):
            boot_log = ""
        report = {
            "schemaVersion": 1,
            "status": "captured-before-preservation",
            "capturedAt": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
            "arm": {
                "completedStages": sorted(arm_passed),
                "failureCategories": sorted(arm_failed),
                "codes": sorted(arm_codes),
            },
            "guestDiagnostics": diagnostic_text.splitlines(),
            "bootDiagnostics": boot_log.splitlines(),
        }
        validate_report(report)
        write_private(args.output, report)
        print("PASS captured sanitized guest, OpenShell, systemd, bootstrap, and boot diagnostics")
        print(f"INFO owner-only workspace diagnostics: {args.output}")
        return 0
    except WorkspaceDiagnosticError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())